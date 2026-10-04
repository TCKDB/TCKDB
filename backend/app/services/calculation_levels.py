"""Shared level-of-theory derivation and validation for role-linked calculations.

Statmech and thermo both link supporting calculations to the record by
**role** (``opt`` / ``freq`` / ``sp`` / ``composite`` / ``imported``), and
both need to answer the same questions from those links. Owner decision,
2026-09 ("statmech-level-roles"): a depositor may legitimately run the
optimisation and the single point at two different levels of theory, and
TCKDB must both *display* that split honestly and *catch* the deposit that
forgets half of it -- on every upload shape, including the multi-conformer
ensemble bundles an ARC-style client actually sends, not only the
standalone one-evidence-chain upload.

Rules, kept in one module so statmech and thermo cannot answer them
differently -- the same discipline
:func:`app.services.statmech_resolution.assert_statmech_role_compatible` /
:func:`app.workflows.thermo.assert_thermo_role_matches_calculation_type`
already follow for role/type compatibility:

* **R1 -- derive.** :func:`derive_levels` answers, at *read* time and
  from the record's role links alone: which level of theory governs the
  geometry (an ``opt``'s), the frequencies (a ``freq``'s, or an ``opt``'s
  own when no ``freq`` is linked but the ``opt`` calculation itself
  carries frequency results), and the energy (ADR 0021, decision 4:
  a linked ``composite``'s level when exactly one level is linked,
  ``"ambiguous"`` when linked ``composite``s disagree; else the linked
  ``sp``s' shared level when they agree, ``"ambiguous"`` when they
  disagree; else an ``opt``'s; else ``imported``). When no ``opt`` /
  ``freq`` is linked, a program-run ``composite`` with its own output
  geometry supplies the geometry and frequency levels from its scheme's
  internal levels, labelled ``composite_recipe``. Never stored,
  never blocking -- purely a projection of whatever is linked right now,
  picking the lowest-id calculation per role for display when more than
  one is linked (the same deterministic tie-break the rest of this
  archive uses, e.g. the statmech provenance-display fallback in
  ``scientific_product_candidacy.md``).
* **R2' -- ensemble-aware multiplicity.** ``opt`` and ``freq`` may repeat
  freely (one pair per conformer is exactly how a multi-conformer
  ensemble product is built). ``sp`` may repeat too, but only when each
  linked ``sp`` sits on a *distinct* linked ``opt``'s output geometry
  (two ``sp``s claiming the same optimisation's geometry is a real
  duplicate) -- :func:`assert_role_consistency` raises the product's
  ``*_role_duplicate`` code for that. All linked ``sp``s must additionally
  share one level of theory; when they do not, "the energy level" has no
  single answer, and that raises the product's `*_energy_level_ambiguous`
  code. With **no** ``opt`` linked there is nothing to anchor on, so the
  same distinctness is asked of *structures*: two ``sp``s (or two
  ``composite``s) on one structure are a duplicate. A polyatomic geometry is
  the same structure as another when one is the other moved rigidly (a
  Kabsch-aligned RMSD within the rounding of the stored coordinates, the
  atoms listed in any order, #667, #679); a single atom has no geometry
  to differ in, so every position of it is one structure, compared by element
  (``D``/``T`` read as hydrogen) and stated isotope mass number (#610, #623).
* **R3' -- every sp must sit on some linked opt's geometry.** Silent
  when either side declares no geometry at all -- absence of evidence is
  not evidence of a mismatch, and with at most one linked ``opt`` there
  is no ambiguity about *which* one to compare against, so an ``sp`` with
  no declared geometry is simply assumed to belong to it.
* **Coverage.** If any ``sp`` is linked, every linked ``opt`` must have
  one on its own geometry -- an ensemble that supplies a refined energy
  for some conformers and not others is exactly the "forgot the SP"
  deposit R4 exists to catch, generalised to more than one conformer.
  Unconditional: this does not require a declared
  ``energy_level_of_theory`` to fire.
* **R4'/R5 -- the declared energy level must be honest.** A depositor may
  declare the level of theory they intend the record's energy to stand
  at. It must equal the linked ``composite``'s level when one is linked, else
  the linked ``sp``s' shared level when any are linked,
  or every linked ``opt``'s level when none are (R5: opt-only is valid
  exactly when there is nothing to contradict).

Composite energies (ADR 0021, decision 4). A ``composite`` calculation is one
energy, so R2', R3', Coverage and R4' apply to it exactly as to an ``sp``, with
three additions:

* a record may not link **both** an ``sp`` and a ``composite`` as its energy
  (``*_energy_sp_and_composite_linked``): two energies for one record is the
  ambiguity these rules exist to remove;
* the composite must sit on a linked ``opt``'s geometry, comparing the geometry
  it ran on, else the one it produced, and comparing nothing when it declares none;
* a linked ``freq`` at a level other than the composite scheme's own frequency
  level is a **warning** (``composite_frequency_level_differs_from_recipe``): the
  recipe's scaled ZPE belongs to its own frequency level.

Only a calculation whose *type* is ``composite`` takes part in those checks. A
calculation of another type linked under the role ``composite`` is the legacy
shape: it is accepted, answered with a ``composite_role_on_non_composite_calculation``
warning (owner decision 7: warn now, refuse once the ARC adapter ships), and
otherwise ignored here exactly as it was before the type existed.

Enforced by default on every write path that links role-tagged source
calculations -- there is no longer an ensemble-vs-standalone split, because
the ensemble-aware rules above are correct for both a single evidence chain
(a list of one) and a genuine multi-conformer bundle.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.api.error_contract import CodedValueError
from app.chemistry.geometry import resolve_element_symbol
from app.chemistry.isotopes import implied_isotope_mass_number
from app.chemistry.permuted_rigid_match import PreparedGeometry, SearchBudget, match_prepared
from app.chemistry.torsion_fingerprint import kabsch_rmsd
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType
from app.db.models.composite_scheme import CompositeScheme, LevelOfTheoryComposite
from app.db.models.geometry import Geometry, GeometryAtom
from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from app.schemas.upload_warning import UploadWarning

#: Which role supplied the energy level -- shared by :class:`DerivedLevels`
#: and the read-schema field it feeds
#: (``app.schemas.reads.scientific_common.ScientificLevelsSummary
#: .energy_source``), so the two cannot silently drift apart on which
#: strings are valid. ``"ambiguous"``: linked ``sp``s (or linked
#: ``composite``s) exist but disagree on level of theory, so no single
#: energy level can be reported.
EnergySource = Literal["sp", "opt", "composite", "imported", "ambiguous"]

#: Where the geometry level came from: a linked ``opt``, or -- when none is
#: linked -- the internal geometry level of a program-run composite's scheme
#: (``"composite_recipe"``). Same sharing rule as :data:`EnergySource`, for
#: ``ScientificLevelsSummary.geometry_source``.
GeometrySource = Literal["opt", "composite_recipe"]

#: Where the frequency level came from: a linked ``freq``, an ``opt`` that
#: carries frequency results itself, or the internal frequency level of a
#: program-run composite's scheme (``"composite_recipe"``). Feeds
#: ``ScientificLevelsSummary.frequency_source``.
FrequencySource = Literal["freq", "opt", "composite_recipe"]

#: A record links two energy calculations on the same optimisation's geometry
#: (R2'). Distinct codes per product so a client branching on ``code`` never has
#: to know which product it asked about.
W_STATMECH_ROLE_DUPLICATE = "statmech_role_duplicate"
W_THERMO_ROLE_DUPLICATE = "thermo_role_duplicate"

#: A linked energy calculation's geometry is not an output geometry of any
#: linked 'opt' (R3'). The name says 'sp' for history; it covers a composite too.
W_STATMECH_SP_GEOMETRY_MISMATCH = "statmech_sp_geometry_mismatch"
W_THERMO_SP_GEOMETRY_MISMATCH = "thermo_sp_geometry_mismatch"

#: Either (a) some linked 'opt' has no covering energy calculation while at
#: least one other does (Coverage), or (b) a declared ``energy_level_of_theory``
#: differs from some linked 'opt's level and no 'sp' / 'composite' is linked at
#: all (R4'). Both name "an opt needs an energy calculation (at this level) and
#: does not have one"; sharing the code keeps a client's remedy the same for
#: either.
W_STATMECH_ENERGY_LEVEL_REQUIRES_SP = "statmech_energy_level_requires_sp"
W_THERMO_ENERGY_LEVEL_REQUIRES_SP = "thermo_energy_level_requires_sp"

#: A declared ``energy_level_of_theory`` disagrees with the linked 'sp' (or
#: 'composite') set's own (shared) level of theory.
W_STATMECH_ENERGY_LEVEL_CONTRADICTION = "statmech_energy_level_contradiction"
W_THERMO_ENERGY_LEVEL_CONTRADICTION = "thermo_energy_level_contradiction"
#: The kinetics counterpart, at the same tier (a 422 coded refusal): a declared
#: ``energy_level_of_theory`` disagrees with the level of a linked reactant, product or
#: transition-state energy calculation.
W_KINETICS_ENERGY_LEVEL_CONTRADICTION = "kinetics_energy_level_contradiction"

#: Two or more linked 'sp's (or 'composite's) disagree on level of theory, so
#: "the energy level" has no single answer (R2').
W_STATMECH_ENERGY_LEVEL_AMBIGUOUS = "statmech_energy_level_ambiguous"
W_THERMO_ENERGY_LEVEL_AMBIGUOUS = "thermo_energy_level_ambiguous"

#: A record links both an 'sp' and a 'composite' as its energy (ADR 0021,
#: decision 4). Two energies for one record is the ambiguity R2' exists to remove.
W_STATMECH_ENERGY_SP_AND_COMPOSITE_LINKED = "statmech_energy_sp_and_composite_linked"
W_THERMO_ENERGY_SP_AND_COMPOSITE_LINKED = "thermo_energy_sp_and_composite_linked"

#: Warn tier. A linked 'freq' is at a level other than the composite scheme's own
#: frequency level.
W_COMPOSITE_FREQUENCY_LEVEL_DIFFERS = "composite_frequency_level_differs_from_recipe"

#: Warn tier. A calculation of a type other than ``composite`` is linked under
#: the role ``composite`` (the legacy shape, from before the type existed).
W_COMPOSITE_ROLE_ON_NON_COMPOSITE = "composite_role_on_non_composite_calculation"


class RoleCalcInfo(NamedTuple):
    """The facts :func:`derive_levels` needs about one role's calculation.

    Deliberately not the ``Calculation`` row itself: callers building this
    from bulk-loaded read-time data (thermo's per-record loop) often have
    no ORM row in hand, only a dict of scalar columns. Keeping this a
    plain tuple lets both the write-time (ORM-backed) and read-time
    (dict-backed) callers share one derivation function.

    The last three fields are only read for a ``composite``-role calculation:
    whether it has an output geometry of its own, and its scheme's internal
    geometry and frequency levels (``None`` where the scheme does not state
    them, or the level is not bound to a scheme).
    """

    lot_id: int | None
    carries_frequencies: bool = False
    has_output_geometry: bool = False
    recipe_geometry_lot_id: int | None = None
    recipe_frequency_lot_id: int | None = None


class DerivedLevels(NamedTuple):
    """R1's answer: which ``level_of_theory.id`` governs each dimension."""

    geometry_lot_id: int | None
    frequency_lot_id: int | None
    energy_lot_id: int | None
    #: Which role supplied ``energy_lot_id``; ``"ambiguous"`` when linked
    #: ``sp``s (or ``composite``s) disagreed (``energy_lot_id`` is then
    #: ``None``); ``None`` when nothing linked can answer it.
    energy_source: EnergySource | None
    #: Where ``geometry_lot_id`` came from; ``None`` when it is ``None``.
    geometry_source: GeometrySource | None = None
    #: Where ``frequency_lot_id`` came from; ``None`` when it is ``None``.
    frequency_source: FrequencySource | None = None


def derive_levels(
    *,
    opts: Sequence[RoleCalcInfo] = (),
    freqs: Sequence[RoleCalcInfo] = (),
    sps: Sequence[RoleCalcInfo] = (),
    composites: Sequence[RoleCalcInfo] = (),
    importeds: Sequence[RoleCalcInfo] = (),
    legacy_composites: Sequence[RoleCalcInfo] = (),
) -> DerivedLevels:
    """R1: derive geometry / frequency / energy levels from role links.

    Pure and DB-free by design -- every caller has already resolved
    whichever calculations it wants to represent each role, from whatever
    source (fresh ORM rows during upload, a bulk-loaded metadata dict at
    read time). This function only encodes the *priority*, so statmech and
    thermo cannot drift apart on what "the energy level" means.

    Each list is read as **lowest-calculation-id first**; callers are
    responsible for that ordering (both write-time ``RoleLink`` sorting
    and the read-time builders already produce it). Only the first
    element of ``opts``/``freqs`` is used for display, on the same
    deterministic tie-break used everywhere else in this archive. Every
    element of ``sps`` and of ``composites`` is used -- to detect
    disagreement, not only to pick one.

    **Energy** (ADR 0021, decision 4): composite > sp > opt > imported. More
    than one distinct composite level is ``"ambiguous"``.

    **Geometry and frequency**: an ``opt`` / ``freq`` as ever. When there is no
    ``opt`` (or no ``freq`` and no frequency-carrying ``opt``), the first linked
    composite that has an output geometry of its own supplies the level from its
    scheme's internal levels, with source ``"composite_recipe"``; a composite
    with no output geometry says nothing about the geometry, and a scheme that
    does not state a level supplies none.

    :param opts: The record's ``opt``-role calculation infos, lowest id
        first.
    :param freqs: The record's ``freq``-role calculation infos, lowest id
        first.
    :param sps: The record's ``sp``-role calculation infos, lowest id
        first.
    :param composites: The record's ``composite``-role calculation infos,
        lowest id first.
    :param importeds: The record's ``imported``-role calculation infos,
        lowest id first.
    :param legacy_composites: Calculations linked under the role ``composite``
        whose *type* is not ``composite`` (the shape from before the type
        existed). They keep the slot they always had -- below an ``opt``, above
        ``imported``, source ``"composite"`` -- and supply no recipe levels:
        nothing says a plain ``sp`` or ``opt`` ran a recipe. Only a calculation
        of type ``composite`` belongs in ``composites``.
    :returns: The derived levels. Any field may be ``None`` when nothing
        linked can answer that question.
    """
    opt = opts[0] if opts else None
    recipe_source = next((c for c in composites if c.has_output_geometry), None)

    geometry_lot_id: int | None
    geometry_source: GeometrySource | None
    if opt is not None:
        geometry_lot_id, geometry_source = opt.lot_id, "opt"
    elif recipe_source is not None and recipe_source.recipe_geometry_lot_id is not None:
        geometry_lot_id, geometry_source = recipe_source.recipe_geometry_lot_id, "composite_recipe"
    else:
        geometry_lot_id, geometry_source = None, None

    frequency_lot_id: int | None
    frequency_source: FrequencySource | None
    if freqs:
        frequency_lot_id, frequency_source = freqs[0].lot_id, "freq"
    elif opt is not None and opt.carries_frequencies:
        frequency_lot_id, frequency_source = opt.lot_id, "opt"
    elif recipe_source is not None and recipe_source.recipe_frequency_lot_id is not None:
        frequency_lot_id, frequency_source = recipe_source.recipe_frequency_lot_id, "composite_recipe"
    else:
        frequency_lot_id, frequency_source = None, None

    energy_lot_id: int | None
    energy_source: EnergySource | None
    # ``None``-lot calculations (one whose level of theory did not resolve)
    # are excluded from the disagreement count -- an unknown level is not
    # evidence of a *different* level, and every real one still gets
    # counted once each.
    distinct_composite_lots = {i.lot_id for i in composites if i.lot_id is not None}
    distinct_sp_lots = {i.lot_id for i in sps if i.lot_id is not None}
    if len(distinct_composite_lots) > 1:
        energy_lot_id, energy_source = None, "ambiguous"
    elif composites:
        energy_lot_id, energy_source = composites[0].lot_id, "composite"
    elif len(distinct_sp_lots) > 1:
        energy_lot_id, energy_source = None, "ambiguous"
    elif sps:
        energy_lot_id, energy_source = sps[0].lot_id, "sp"
    elif opt is not None:
        energy_lot_id, energy_source = opt.lot_id, "opt"
    elif legacy_composites:
        energy_lot_id, energy_source = legacy_composites[0].lot_id, "composite"
    elif importeds:
        energy_lot_id, energy_source = importeds[0].lot_id, "imported"
    else:
        energy_lot_id, energy_source = None, None

    return DerivedLevels(
        geometry_lot_id=geometry_lot_id,
        frequency_lot_id=frequency_lot_id,
        energy_lot_id=energy_lot_id,
        energy_source=energy_source,
        geometry_source=geometry_source,
        frequency_source=frequency_source,
    )


@dataclass(frozen=True)
class RoleLink:
    """One role-tagged source-calculation link, as resolved during upload.

    ``role`` is compared by its plain string value so this stays usable
    from both ``StatmechCalculationRole`` and ``ThermoCalculationRole``
    without importing either enum here.
    """

    role: str
    calculation: Calculation


def _by_role(links: list[RoleLink], role: str) -> list[Calculation]:
    """Every linked calculation for *role*, lowest ``id`` first, deduplicated.

    Deduplicated because the same calculation can legitimately reach here
    twice under two different local names (an inline key and, in a
    future upload, a chained id) -- and because two of this module's
    counts (role-of-theory ambiguity, coverage) would otherwise double-
    count one calculation cited twice.
    """
    seen: dict[int, Calculation] = {}
    for link in links:
        if link.role == role:
            seen[link.calculation.id] = link.calculation
    return sorted(seen.values(), key=lambda calc: calc.id)


def _composite_calcs(links: list[RoleLink]) -> list[Calculation]:
    """The linked calculations that are composite energies: role *and* type.

    A calculation linked under the role ``composite`` whose type is something
    else is the legacy shape (see the module docstring); it is not a composite
    energy for any rule here.
    """
    return [calc for calc in _by_role(links, "composite") if calc.type == CalculationType.composite]


def _recipe_levels(calc: Calculation) -> tuple[int | None, int | None]:
    """``(geometry, frequency)`` internal level ids of a composite's scheme.

    ``(None, None)`` when the calculation's level of theory is bound to no
    scheme, or the scheme does not state them.
    """
    session = object_session(calc)
    if session is None or calc.lot_id is None:
        return (None, None)
    row = session.execute(
        select(CompositeScheme.geometry_level_of_theory_id, CompositeScheme.frequency_level_of_theory_id)
        .join(LevelOfTheoryComposite, LevelOfTheoryComposite.scheme_id == CompositeScheme.id)
        .where(LevelOfTheoryComposite.level_of_theory_id == calc.lot_id)
    ).first()
    return (None, None) if row is None else (row[0], row[1])


def role_calc_infos(links: list[RoleLink], role: str) -> list[RoleCalcInfo]:
    """The ``RoleCalcInfo`` list :func:`derive_levels` wants for *role*.

    Exported so write-time callers (already holding ``RoleLink``s) can
    feed the same derivation function the read-time builders use,
    without duplicating the "carries frequencies" check. For the
    ``composite`` role the info also carries whether the calculation has an
    output geometry and its scheme's internal levels.
    """
    infos: list[RoleCalcInfo] = []
    for calc in _by_role(links, role):
        if role == "composite":
            recipe_geometry, recipe_frequency = _recipe_levels(calc)
            infos.append(
                RoleCalcInfo(
                    lot_id=calc.lot_id,
                    carries_frequencies=calc.freq_result is not None,
                    has_output_geometry=bool(calc.output_geometries),
                    recipe_geometry_lot_id=recipe_geometry,
                    recipe_frequency_lot_id=recipe_frequency,
                )
            )
        else:
            infos.append(RoleCalcInfo(lot_id=calc.lot_id, carries_frequencies=calc.freq_result is not None))
    return infos


def _lot_label(lot: LevelOfTheory | None) -> str:
    """``method/basis`` (or bare ``method``), the way a chemist writes a LoT.

    Mirrors ``LevelOfTheorySummary.display`` -- for a refusal message, not
    a stored value.
    """
    if lot is None:
        return "unknown"
    return f"{lot.method}/{lot.basis}" if lot.basis else lot.method


def _calc_geometry_ids(calc: Calculation, *, role: str) -> set[int]:
    """The geometry ids a linked energy calculation declares, for R3'.

    An ``sp`` is compared by the geometry it ran on (its input link), which is
    what R3' has always done. A composite is compared by the geometry it ran
    on, else the one it produced: a program-run composite optimises its own
    geometry, so for the common primary shape the output link is the only one.
    """
    inputs = {row.geometry_id for row in calc.input_geometries}
    if role == "composite" and not inputs:
        return {row.geometry_id for row in calc.output_geometries}
    return inputs


def _atom_nuclide(atom: GeometryAtom) -> tuple[str, int | None]:
    """``(element, isotope mass number)`` of one stored atom: the atom's own notion of "the same atom".

    ``element`` is CHAR(2), so a one-letter symbol comes back blank-padded, and
    ``D``/``T`` are hydrogen for any element comparison (the composition check
    reads them so), hence ``resolve_element_symbol``. The isotope is the stored
    mass number, which ``parse_xyz`` sets for every row written since
    ``docs/adr/0022``; a row written before it holds ``D``/``T`` with a NULL
    mass number and cannot be rewritten, so its own symbol is read, and a legacy
    deuterium atom and a new one are one atom.
    """
    mass_number = atom.isotope_mass_number
    if mass_number is None:
        mass_number = implied_isotope_mass_number(atom.element)
    return resolve_element_symbol(atom.element.strip()), mass_number


def _structure_key(geometry: Geometry) -> tuple[str | int | None, ...]:
    """What makes two energy calculations' geometries different structures (R2', no opt).

    A **single atom** has no geometry to differ in -- every position is the
    same structure -- so it is keyed by its element (D and T resolve to
    hydrogen) and its isotope mass number (stated, or implied by a D/T spelling
    on a row written before ``docs/adr/0022``), and a copy of the atom moved to
    another coordinate is the same structure, not a second one (#623).

    A polyatomic geometry is keyed by its row id *here*, and
    :func:`_energies_by_structure` then merges rows that are the same structure
    moved rigidly (#667, :func:`_same_polyatomic_structure`). The key is only
    the starting point for that merge, never the answer.

    The element rather than a conformer observation, because the element is
    read from the very geometry the energy calculation declares, so the key is
    always available when the comparison is, on every route (the standalone
    thermo/statmech uploads have no conformer observation at all). A record
    with no linked opt describes one subject, so the element alone cannot
    conflate two species.
    """
    if geometry.natoms == 1 and geometry.atoms:
        atom = geometry.atoms[0]
        element, mass_number = _atom_nuclide(atom)
        return ("atom", element, mass_number)
    return ("geometry", geometry.id)


#: Decimals every stored coordinate is formatted to: ``parse_xyz`` writes
#: ``geometry.xyz_text`` (the text ``geom_hash`` is taken over) with ``.12f``.
#: No stored coordinate carries information below this.
_STORED_COORDINATE_DECIMALS = 12

#: The fewest decimals a geometry is credited with. A geometry whose
#: coordinates are all multiples of 1e-2 (a toy diatomic, integers) would
#: otherwise earn a tolerance wide enough to merge genuinely different
#: structures; coarse deposits are held to the tolerance of a 4-decimal one, so
#: the rule errs toward "different" (the status quo) rather than toward a false
#: refusal.
_MIN_COORDINATE_DECIMALS = 4


def _coordinate_decimals(coordinates: Sequence[tuple[float, float, float]]) -> int:
    """The decimals a geometry's coordinates were actually written to.

    Stored coordinates are doubles formatted to 12 decimals, so a deposit
    written to six decimals reads back as ``0.123456000000``; the last
    non-zero decimal over all coordinates is the precision the depositor
    had. Clamped to ``[_MIN_COORDINATE_DECIMALS, _STORED_COORDINATE_DECIMALS]``.
    """
    decimals = 0
    for coordinate in coordinates:
        for value in coordinate:
            text = f"{value:.{_STORED_COORDINATE_DECIMALS}f}".rstrip("0")
            decimals = max(decimals, len(text.split(".")[1]) if "." in text else 0)
    return max(_MIN_COORDINATE_DECIMALS, min(decimals, _STORED_COORDINATE_DECIMALS))


def _rigid_motion_tolerance(decimals_a: int, decimals_b: int) -> float:
    """The largest Kabsch RMSD (Angstrom) two copies of one structure can show.

    Two deposits of one structure moved rigidly differ only by the rounding of
    each: a coordinate written to ``d`` decimals is within ``0.5 * 10**-d`` of
    the true one, so an atom is within ``sqrt(3) * 0.5 * 10**-d`` (three
    components). The two displacements add at worst, and a root-mean-square
    over atoms cannot exceed the worst atom; Kabsch minimises the RMSD, so it
    cannot exceed the RMSD under the true motion. Hence
    ``sqrt(3) / 2 * (10**-d_a + 10**-d_b)``. For two 12-decimal geometries
    that is ~1.7e-12 A; for two 6-decimal ones ~1.7e-6 A.
    """
    return math.sqrt(3.0) / 2.0 * (10.0 ** -decimals_a + 10.0 ** -decimals_b)


class _AtomsOf(NamedTuple):
    species: tuple[tuple[str, int | None], ...]
    coordinates: list[tuple[float, float, float]]
    decimals: int


def _atoms_of(geometry: Geometry) -> _AtomsOf | None:
    """A geometry's atoms in stored order, or ``None`` when they are not all on file."""
    rows = sorted(geometry.atoms, key=lambda atom: atom.atom_index)
    if not rows or len(rows) != geometry.natoms:
        return None
    coordinates = [(row.x, row.y, row.z) for row in rows]
    # Element and explicit isotope per atom, the atom path's own notion of "the same atom" (#663).
    species = tuple(_atom_nuclide(row) for row in rows)
    return _AtomsOf(species, coordinates, _coordinate_decimals(coordinates))


def _same_polyatomic_structure(
    a: _AtomsOf,
    b: _AtomsOf,
    budget: SearchBudget | None = None,
    prepared: tuple[PreparedGeometry, PreparedGeometry] | None = None,
) -> bool:
    """Whether two geometries are one structure moved rigidly (#667), atoms in any order (#679).

    Same elements and stated isotopes in the same order are compared directly
    (Kabsch). When that does not match and the two hold the same atoms (the same
    nuclide counts), a relabelling of ``b`` is searched for that makes it a rigid
    copy of ``a`` (:func:`find_matching_permutation`), then compared with the same
    Kabsch test and the same tolerance. Only atoms of one nuclide are exchanged, so
    a ``2H`` is never taken for an ``1H``.

    Kabsch alignment allows a proper rotation only, so a mirror image (a
    different enantiomer) stays a different structure whatever the atom order.
    The relabelling search is bounded; a pair on which it hits a bound is
    *different* (the behaviour before the search), never a match.
    """
    tolerance = _rigid_motion_tolerance(a.decimals, b.decimals)
    if a.species == b.species and kabsch_rmsd(a.coordinates, b.coordinates) <= tolerance:
        return True
    if Counter(a.species) != Counter(b.species):
        return False
    first, second = prepared or (
        PreparedGeometry(a.coordinates, a.species),
        PreparedGeometry(b.coordinates, b.species),
    )
    found = match_prepared(first, second, tolerance=tolerance, budget=budget)
    return found.outcome == "matched"


def _merge_rigidly_equal_geometries(geometries: Sequence[Geometry]) -> dict[int, int]:
    """Map each polyatomic geometry id to a representative id of its structure.

    Pairwise over the record's own geometries (a handful), and only within a
    group of equal atom count, so an atom list is read from the database
    only for a geometry that has a same-size neighbour to be compared with.
    One :class:`SearchBudget` is shared by every pair, and a pair already in one
    structure through a third geometry is not compared again. The relabelling
    search (#679) is charged to that budget: a gate on the sorted distance list
    (about n^2/2 units per pair), the candidate table (n^3) and each fit (4 n^2).
    What is *not* charged is building each geometry's distance tables once,
    O(n^2 log n) per geometry, linear in the number of geometries; so the cost is
    that plus a fixed budget, not a quantity that grows with the number of pairs.
    """
    root = {geometry.id: geometry.id for geometry in geometries}
    budget = SearchBudget()
    prepared: dict[int, PreparedGeometry] = {}

    def tables(geometry_id: int, atoms: _AtomsOf) -> PreparedGeometry:
        if geometry_id not in prepared:
            prepared[geometry_id] = PreparedGeometry(atoms.coordinates, atoms.species)
        return prepared[geometry_id]

    def find(i: int) -> int:
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i

    by_size: dict[int, list[Geometry]] = {}
    for geometry in geometries:
        by_size.setdefault(geometry.natoms, []).append(geometry)
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        atoms = {geometry.id: _atoms_of(geometry) for geometry in same_size}
        for i, first in enumerate(same_size):
            for second in same_size[i + 1 :]:
                if find(first.id) == find(second.id):
                    continue
                a, b = atoms[first.id], atoms[second.id]
                if a is None or b is None:
                    continue
                pair = (tables(first.id, a), tables(second.id, b))
                if _same_polyatomic_structure(a, b, budget, pair):
                    root[find(second.id)] = find(first.id)
    return {geometry.id: find(geometry.id) for geometry in geometries}


def _energies_by_structure(
    energies: Sequence[Calculation],
) -> dict[tuple[str | int | None, ...], list[Calculation]]:
    """Group energy calculations by the structure they ran on (R2', no opt).

    An atom is grouped by :func:`_structure_key`; a polyatomic geometry by the
    structure its row belongs to once rigidly moved copies are merged.
    """
    keyed: list[tuple[Calculation, tuple[str | int | None, ...]]] = []
    polyatomic: dict[int, Geometry] = {}
    for energy in energies:
        # The geometry an energy calculation ran on: its input link, else
        # (the network route links a geometry as a calculation's final
        # output only) its output link.
        geometry_links = energy.input_geometries or energy.output_geometries
        for row in geometry_links:
            key = _structure_key(row.geometry)
            keyed.append((energy, key))
            if key[0] == "geometry":
                polyatomic[row.geometry.id] = row.geometry
    representative = _merge_rigidly_equal_geometries(list(polyatomic.values()))
    by_structure: dict[tuple[str | int | None, ...], list[Calculation]] = {}
    for energy, key in keyed:
        if key[0] == "geometry":
            key = ("geometry", representative[key[1]])  # type: ignore[index]
        claimants = by_structure.setdefault(key, [])
        if energy not in claimants:
            claimants.append(energy)
    return by_structure


def _match_energy_to_opts(
    energy_geometry_ids: set[int],
    opts: list[Calculation],
    opt_output_geoms: dict[int, set[int]],
) -> list[Calculation] | None:
    """Which linked ``opt``s an energy calculation's geometry evidence covers (R3').

    Three outcomes:

    * ``None`` -- not comparable: no ``opt`` is linked at all, or
      geometry data is absent on the energy calculation and/or on every ``opt``.
      Absence of evidence is never treated as a mismatch (house rule);
      the caller skips this calculation entirely rather than counting or
      blaming it.
    * ``[]`` -- comparable data existed and disagreed: a genuine R3'
      violation. The caller raises.
    * non-empty list -- the ``opt``(s) this calculation's geometry matches.
      With exactly one linked ``opt`` this is always that one once any
      data is absent on either side (there is no ambiguity about *which*
      opt to assume); with more than one, only ``opt``s whose declared
      output geometry the calculation's declared geometry intersects.
    """
    if not opts:
        return None
    comparable_opts = [opt for opt in opts if opt_output_geoms[opt.id]]
    if not energy_geometry_ids or not comparable_opts:
        return [opts[0]] if len(opts) == 1 else None
    return [opt for opt in comparable_opts if opt_output_geoms[opt.id] & energy_geometry_ids]


def collect_composite_link_warnings(links: list[RoleLink], *, subject: str) -> list[UploadWarning]:
    """The warn-tier findings about a record's composite links (ADR 0021, decision 7).

    * ``composite_role_on_non_composite_calculation`` -- a calculation whose type is
      not ``composite`` is linked under the role ``composite``. The legacy
      shape: stored as sent; will be refused once the ARC adapter ships the
      composite shape.
    * ``composite_frequency_level_differs_from_recipe`` -- a linked ``freq``
      calculation is at a level other than the frequency level of the
      composite scheme the record's composite is bound to. The recipe's scaled
      zero-point energy belongs to its own frequency level, so a record that
      combines the two is worth a second look; it is an expectation, not a
      definition, so it warns.

    :param links: Every role link resolved for the record.
    :param subject: ``"statmech"`` or ``"thermo"``, for messages.
    """
    warnings: list[UploadWarning] = []
    legacy = [
        calc
        for calc in _by_role(links, "composite")
        if calc.type != CalculationType.composite
    ]
    for calc in legacy:
        warnings.append(
            UploadWarning(
                field="source_calculations",
                code=W_COMPOSITE_ROLE_ON_NON_COMPOSITE,
                message=(
                    f"{subject}: a calculation of type '{calc.type.value}' is linked with the role "
                    "'composite'. The role names a composite energy, which is recorded by a "
                    "calculation of type 'composite'. The link was stored as sent; it will be "
                    "refused once producers can send that shape."
                ),
            )
        )
    freqs = _by_role(links, "freq")
    if freqs:
        for composite in _composite_calcs(links):
            _geometry_lot, frequency_lot = _recipe_levels(composite)
            if frequency_lot is None:
                continue
            session = object_session(composite)
            if session is None:
                continue
            canonical = canonical_level_of_theory_id_for_write(session, frequency_lot)
            differing = [f for f in freqs if f.lot_id is not None and f.lot_id != canonical]
            if differing:
                warnings.append(
                    UploadWarning(
                        field="source_calculations",
                        code=W_COMPOSITE_FREQUENCY_LEVEL_DIFFERS,
                        message=(
                            f"{subject}: a linked 'freq' calculation is at "
                            f"{_lot_label(differing[0].lot)}, but the composite method "
                            f"{_lot_label(composite.lot)} runs its frequencies at "
                            f"{_lot_label(session.get(LevelOfTheory, canonical))} and scales their "
                            "zero-point energy for that level. Link the frequency calculation at the "
                            "recipe's own level, or expect the record's frequencies and its energy to "
                            "describe different levels."
                        ),
                    )
                )
                break
    return warnings


def canonical_level_of_theory_id_for_write(session: Session, level_of_theory_id: int) -> int:
    """The level a stored id stands for once merges are followed (write-time)."""
    merged_into = session.scalar(
        select(LevelOfTheoryMerge.into_lot_id).where(LevelOfTheoryMerge.merged_lot_id == level_of_theory_id)
    )
    return merged_into if merged_into is not None else level_of_theory_id


def assert_role_consistency(
    links: list[RoleLink],
    declared: LevelOfTheory | None,
    *,
    duplicate_code: str,
    geometry_mismatch_code: str,
    requires_sp_code: str,
    contradiction_code: str,
    ambiguous_code: str,
    sp_and_composite_code: str,
    subject: str,
    warnings: list[UploadWarning] | None,
) -> None:
    """R2'/R3'/Coverage/R4': the full ensemble-aware role-consistency check.

    One call replaces the four separate assertions this module used to
    expose, because the four questions share the same sp-to-opt geometry
    matching pass and answering them separately either recomputed it four
    times or forced a caller to thread the intermediate state through
    itself.

    **Precedence, when a deposit is wrong in more than one way at once**:
    an ``sp`` and a ``composite`` both linked as energy (decision 4: the
    record has two energies) first, then R3' (a genuine geometry mismatch),
    then R2' distinctness (two energy calculations on the same opt's
    geometry), then R2' level uniformity (ambiguous),
    then Coverage, then R4'. A deposit that BOTH puts two sps on the same
    opt's geometry AND has those two sps disagree on level of theory
    (both are true of the same pair) is reported as the duplicate --
    ``*_role_duplicate``, not ``*_energy_level_ambiguous`` -- because
    distinctness is checked, and raised, first. This is not accidental:
    "two sps claim one optimisation" is the more specific fact and the
    one whose fix (remove the extra link) also fixes the level
    disagreement as a side effect, so it is the more useful first thing
    to tell a depositor.

    A linked ``composite`` (type ``composite``) is the record's energy
    calculation in place of the ``sp``s, and every rule below applies to it
    as to an ``sp``; messages name the role they are about.

    :param links: Every role link resolved for this upload (or bundle
        block) -- every linked ``opt``/``freq``/``sp``/``composite``/
        ``imported`` calculation, from every path that produced one.
    :param declared: The resolved ``energy_level_of_theory``, or ``None``
        when the depositor did not declare one (the field does not exist
        on every wire model this is called from -- see the module
        docstring).
    :param duplicate_code: Coded refusal for "two energy calculations on one
        opt's geometry" (R2').
    :param geometry_mismatch_code: Coded refusal for "an energy calculation
        not on any linked opt's geometry" (R3').
    :param requires_sp_code: Coded refusal for "an opt has no covering energy
        calculation while another does" (Coverage) and for "declared level
        differs from some opt's and no energy calculation is linked at all"
        (R4').
    :param contradiction_code: Coded refusal for "declared level disagrees
        with the linked energy calculations' own (shared) level" (R4').
    :param ambiguous_code: Coded refusal for "linked energy calculations
        disagree on level of theory" (R2').
    :param sp_and_composite_code: Coded refusal for "an sp and a composite are
        both linked as the energy" (decision 4).
    :param subject: ``"statmech"`` or ``"thermo"``, for messages.
    :param warnings: Out-list for the warn-tier findings about composite links
        (:func:`collect_composite_link_warnings`). Required, with ``None``
        meaning "this caller has nowhere to report them": every caller states
        which, so a new call site cannot silently drop them.
    :raises CodedValueError: per the cases above.
    """
    if warnings is not None:
        warnings.extend(collect_composite_link_warnings(links, subject=subject))

    opts = _by_role(links, "opt")
    sps = _by_role(links, "sp")
    composites = _composite_calcs(links)

    if sps and composites:
        raise CodedValueError(
            sp_and_composite_code,
            f"{subject}: this record links both 'sp' calculation(s) "
            f"({', '.join(sp.public_ref for sp in sps)}) and 'composite' calculation(s) "
            f"({', '.join(c.public_ref for c in composites)}) as its energy. A record has one "
            "energy: a composite energy already includes its own single points, so link the "
            "composite alone, or the single point alone, or split the record.",
            context={
                "sp_calculation_refs": [sp.public_ref for sp in sps],
                "composite_calculation_refs": [c.public_ref for c in composites],
            },
            message_prefix=False,
        )

    # The record's energy calculations: the composites when there are any
    # (the two kinds are never linked together past the check above), else the sps.
    energy_role = "composite" if composites else "sp"
    energies = composites if composites else sps
    # Wording only: an ``sp`` is compared by the geometry it ran on, as ever,
    # so its messages keep saying "input geometry" and "an 'sp'".
    geometry_word = "input geometry" if energy_role == "sp" else "geometry"
    article = "an" if energy_role == "sp" else "a"

    opt_output_geoms = {
        opt.id: {row.geometry_id for row in opt.output_geometries} for opt in opts
    }
    opt_covering: dict[int, list[Calculation]] = {opt.id: [] for opt in opts}

    for energy in energies:
        matched = _match_energy_to_opts(_calc_geometry_ids(energy, role=energy_role), opts, opt_output_geoms)
        if matched is None:
            continue
        if not matched:
            raise CodedValueError(
                geometry_mismatch_code,
                f"{subject}: the linked '{energy_role}' calculation ({energy.public_ref}) "
                "was not run on a geometry any linked 'opt' calculation "
                f"produced. Link the '{energy_role}' role to a calculation whose "
                f"{geometry_word} is one of a linked optimisation's output "
                "geometries.",
                context={f"{energy_role}_calculation_ref": energy.public_ref},
                message_prefix=False,
            )
        for opt in matched:
            opt_covering[opt.id].append(energy)

    # R2' distinctness: no opt's geometry may be claimed by more than one energy calculation.
    for opt in opts:
        covering = opt_covering[opt.id]
        if len(covering) > 1:
            refs = [energy.public_ref for energy in covering]
            raise CodedValueError(
                duplicate_code,
                f"{subject}: {len(covering)} '{energy_role}' links ({', '.join(refs)}) "
                f"claim the same optimisation's geometry ({opt.public_ref}), "
                f"but a {subject} record may have at most one '{energy_role}' per "
                "optimisation. Remove the extra link.",
                context={
                    "opt_calculation_ref": opt.public_ref,
                    f"{energy_role}_calculation_refs": refs,
                },
                message_prefix=False,
            )

    # R2' distinctness with no optimisation linked (#610, #623). Every check
    # above is anchored on a linked opt, so a record that links only sps (a
    # single atom's honest shape: its sp is its primary and it has no opt)
    # would escape it. The same fact is still a duplicate: two sps run on one
    # structure. Energies are grouped by *structure* (:func:`_structure_key`),
    # not by geometry row: for one atom every position is the same structure,
    # so a second sp on a shifted copy of the atom is the same duplicate. A
    # polyatomic geometry is the same structure as another when one is the
    # other moved rigidly (#667): translated, rotated, atoms listed in any order (#679).
    # An sp that declares no geometry is not compared (absence of evidence is not
    # a match), as everywhere in this module.
    if not opts:
        by_structure = _energies_by_structure(energies)
        for (kind, *_), claimants in by_structure.items():
            if len(claimants) > 1:
                refs = [energy.public_ref for energy in claimants]
                same = "the same atom" if kind == "atom" else "the same geometry"
                per = "atom" if kind == "atom" else "geometry"
                raise CodedValueError(
                    duplicate_code,
                    f"{subject}: {len(claimants)} '{energy_role}' links ({', '.join(refs)}) "
                    f"ran on {same} and no optimisation is linked, "
                    f"but a {subject} record may have at most one '{energy_role}' per "
                    f"{per}. Remove the extra link.",
                    context={f"{energy_role}_calculation_refs": refs},
                    message_prefix=False,
                )

    # R2' level-of-theory uniformity across every linked energy calculation.
    # One with no resolved level of theory is excluded from the disagreement
    # count -- see the identical exclusion in :func:`derive_levels`.
    distinct_energy_lot_ids = {energy.lot_id for energy in energies if energy.lot_id is not None}
    if len(distinct_energy_lot_ids) > 1:
        refs = [energy.public_ref for energy in energies]
        raise CodedValueError(
            ambiguous_code,
            f"{subject}: the linked '{energy_role}' calculations ({', '.join(refs)}) "
            "run at more than one level of theory, so this record's "
            f"energy level has no single answer. Link every '{energy_role}' at the "
            "same level, or split this record so each level gets its own.",
            context={f"{energy_role}_calculation_refs": refs},
            message_prefix=False,
        )

    # Coverage: any energy calculation at all obliges every opt to have
    # one. Unconditional -- this is the "forgot the SP for one conformer"
    # deposit, and it is exactly as wrong whether or not a level was ever declared.
    if energies:
        uncovered = [opt for opt in opts if not opt_covering[opt.id]]
        if uncovered:
            refs = [opt.public_ref for opt in uncovered]
            noun = "optimisation" if len(refs) == 1 else "optimisations"
            verb = "has" if len(refs) == 1 else "have"
            pronoun = "its" if len(refs) == 1 else "their"
            energy_refs = [energy.public_ref for energy in energies]
            if len(uncovered) == len(opts):
                # No energy calculation could be matched to *any* linked
                # optimisation's output geometry at all -- not "one conformer
                # forgot its sp", but "none of the linked calculations carry
                # geometry evidence that reaches any linked opt". A different
                # fact, so a different sentence: naming "at least one other
                # optimisation... does" would be false here.
                raise CodedValueError(
                    requires_sp_code,
                    f"{subject}: {len(refs)} {noun} ({', '.join(refs)}) "
                    f"{verb} no '{energy_role}' calculation linked at {pronoun} geometry, "
                    f"and none of the linked '{energy_role}' calculations "
                    f"({', '.join(energy_refs)}) could be matched to any "
                    f"linked optimisation's output geometry. Link {article} "
                    f"'{energy_role}' whose {geometry_word} is one of these optimisations' "
                    f"output geometries, or declare no '{energy_role}' at all.",
                    context={
                        "uncovered_opt_calculation_refs": refs,
                        f"{energy_role}_calculation_refs": energy_refs,
                    },
                    message_prefix=False,
                )
            raise CodedValueError(
                requires_sp_code,
                f"{subject}: {len(refs)} {noun} ({', '.join(refs)}) "
                f"{verb} no '{energy_role}' calculation linked at {pronoun} geometry, but "
                "at least one other optimisation in this record does. "
                f"Link {article} '{energy_role}' for every optimisation this record's "
                "energy claims, or none.",
                context={"uncovered_opt_calculation_refs": refs},
                message_prefix=False,
            )

    if declared is None:
        return

    if energies:
        energy_lot_id = next(iter(distinct_energy_lot_ids), None)
        if energy_lot_id != declared.id:
            energy = energies[0]
            raise CodedValueError(
                contradiction_code,
                f"{subject}: the declared energy level of theory "
                f"({_lot_label(declared)}) does not match the linked '{energy_role}' "
                f"calculations' level ({_lot_label(energy.lot)}). Declare the "
                f"level the linked {'composite energies' if energy_role == 'composite' else 'single points'} "
                f"actually ran at, or link '{energy_role}' calculations run at the declared level.",
                context={
                    "declared_level_of_theory_ref": declared.public_ref,
                    f"{energy_role}_calculation_refs": [e.public_ref for e in energies],
                    f"{energy_role}_level_of_theory_ref": (
                        energy.lot.public_ref if energy.lot is not None else None
                    ),
                },
                message_prefix=False,
            )
        return

    if opts:
        mismatched = [opt for opt in opts if opt.lot_id != declared.id]
        if mismatched:
            refs = [opt.public_ref for opt in mismatched]
            levels = ", ".join(
                f"{opt.public_ref} ({_lot_label(opt.lot)})" for opt in mismatched
            )
            raise CodedValueError(
                requires_sp_code,
                f"{subject}: energy level of theory {_lot_label(declared)} "
                f"differs from the optimisation level -- {levels} -- but "
                f"no single-point calculation at {_lot_label(declared)} "
                "is linked.",
                context={
                    "declared_level_of_theory_ref": declared.public_ref,
                    "opt_calculation_refs": refs,
                },
                message_prefix=False,
            )


_KINETICS_ENERGY_ROLES = frozenset({"reactant_energy", "product_energy", "ts_energy"})


def assert_kinetics_energy_level_consistency(
    links: list[RoleLink], declared: LevelOfTheory | None
) -> None:
    """A declared kinetics energy level must match every linked energy calculation's level.

    The kinetics counterpart of the R4' check at the end of :func:`assert_role_consistency`
    and refused the same way (a coded 422). Only the ``reactant_energy``, ``product_energy``
    and ``ts_energy`` links are energies; a calculation with no level of theory of its own is
    not compared (absence is not a contradiction), and no declaration, or no energy link, is
    nothing to check. Nothing is inferred in either direction.

    :raises CodedValueError: ``kinetics_energy_level_contradiction``.
    """
    if declared is None:
        return
    energies = [link.calculation for link in links if link.role in _KINETICS_ENERGY_ROLES]
    differing = [c for c in energies if c.lot_id is not None and c.lot_id != declared.id]
    if not differing:
        return
    raise CodedValueError(
        W_KINETICS_ENERGY_LEVEL_CONTRADICTION,
        f"kinetics: the declared energy level of theory ({_lot_label(declared)}) does not match "
        f"the level of the linked energy calculations ({', '.join(c.public_ref for c in differing)} "
        f"at {', '.join(sorted({_lot_label(c.lot) for c in differing}))}). Declare the level the "
        "linked energies ran at, or link energy calculations run at the declared level.",
        context={
            "declared_level_of_theory_ref": declared.public_ref,
            "energy_calculation_refs": [c.public_ref for c in differing],
        },
        message_prefix=False,
    )


__all__ = [
    "W_COMPOSITE_FREQUENCY_LEVEL_DIFFERS",
    "W_COMPOSITE_ROLE_ON_NON_COMPOSITE",
    "W_KINETICS_ENERGY_LEVEL_CONTRADICTION",
    "W_STATMECH_ENERGY_LEVEL_AMBIGUOUS",
    "W_STATMECH_ENERGY_LEVEL_CONTRADICTION",
    "W_STATMECH_ENERGY_LEVEL_REQUIRES_SP",
    "W_STATMECH_ENERGY_SP_AND_COMPOSITE_LINKED",
    "W_STATMECH_ROLE_DUPLICATE",
    "W_STATMECH_SP_GEOMETRY_MISMATCH",
    "W_THERMO_ENERGY_LEVEL_AMBIGUOUS",
    "W_THERMO_ENERGY_LEVEL_CONTRADICTION",
    "W_THERMO_ENERGY_LEVEL_REQUIRES_SP",
    "W_THERMO_ENERGY_SP_AND_COMPOSITE_LINKED",
    "W_THERMO_ROLE_DUPLICATE",
    "W_THERMO_SP_GEOMETRY_MISMATCH",
    "DerivedLevels",
    "EnergySource",
    "FrequencySource",
    "GeometrySource",
    "RoleCalcInfo",
    "RoleLink",
    "assert_kinetics_energy_level_consistency",
    "assert_role_consistency",
    "collect_composite_link_warnings",
    "derive_levels",
    "role_calc_infos",
]
