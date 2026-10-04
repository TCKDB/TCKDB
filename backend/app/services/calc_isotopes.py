"""Isotope agreement between a calculation's geometry and its subject (#666).

Companion of :mod:`app.services.calculation_geometry_composition`, which counts
elements and reads ``D``, ``T`` and ``[2H]`` as hydrogen (an element answer)
and so cannot see an isotope disagreement; this rule reads the nuclide a ``D``/``T``
spelling declares (#672). Not a separate register entry: it is the composition
entry's claim ("this geometry is this species") extended to isotopes, and is
recorded there (``CHECK_CALCULATION_GEOMETRY_COMPOSITION``); only its refusal
code is its own.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.chemistry.geometry import resolve_element_symbol
from app.chemistry.isotopes import (
    implied_isotope_mass_number,
    normalize_isotope,
    render_isotope_substitutions,
)
from app.chemistry.species import isotope_substitutions
from app.db.models.calculation import Calculation
from app.db.models.common import MoleculeKind
from app.db.models.geometry import GeometryAtom
from app.db.models.species import Species, SpeciesEntry
from app.services.calculation_geometry_composition import _ts_entry_reactant_rows

logger = logging.getLogger(__name__)

#: A geometry linked to a calculation carries different isotopic substitutions
#: than the subject the calculation is filed under.
W_CALCULATION_GEOMETRY_ISOTOPE_MISMATCH = "calculation_geometry_isotope_mismatch"

_ISOTOPE_CACHE_KEY = "_calculation_geometry_isotope_reference_cache"

IsotopeCounts = dict[tuple[str, int], int]


def entry_isotope_counts(entry: SpeciesEntry, species: Species) -> IsotopeCounts | None:
    """Return the ``(element, mass_number)`` counts a species entry declares.

    The entry's isotopes live on ``species_entry.isotope_key`` -- the canonical
    isotope-labelled SMILES -- and, on every row written since #66, **not** on
    ``species.smiles``, which is stripped of labels (species identity is shared
    by every isotopologue). ``NULL`` is the all-standard entry, i.e. an empty
    mapping.

    **Legacy fallback (#680).** A species stored before #66 (2026-07-31) kept
    its isotope labels in ``species.smiles`` and has no ``isotope_key``; the
    migration that added the column deliberately did not backfill it (the
    label was free text, deriving a key would be a guess). So when
    ``isotope_key`` is ``NULL`` the species SMILES is read for labels, as
    :func:`app.services.consistency.stoichiometry.entry_facts` reads both
    sources. The two differ in one way: ``entry_facts`` ORs the key and the
    label (it only asks "are there isotopes at all"), whereas here the key wins
    when present and the SMILES is consulted only when the key is ``NULL``,
    because a count needs one source, not two. For a row written after #66 the
    SMILES carries no label, so the fallback yields the same empty mapping and
    changes nothing.

    Shared with :mod:`app.services.hessian_reanalysis`, so the upload check and
    the read-time check cannot disagree about what an entry declares.

    An unparseable key or SMILES is an absence, not a refusal.
    """

    if entry.isotope_key is not None:
        source, text = "isotope_key", entry.isotope_key
    else:
        source, text = "species.smiles", species.smiles
    try:
        return isotope_substitutions(text)
    except ValueError:
        logger.warning(
            "Unparseable %s on species entry id=%s: %r; calculation "
            "geometry isotopes not judged.",
            source,
            entry.id,
            text,
        )
        return None


def _species_entry_isotope_reference(
    session: Session, species_entry_id: int
) -> IsotopeCounts | None:
    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        return None
    species = session.get(Species, entry.species_id)
    if species is None or species.kind == MoleculeKind.pseudo:
        return None
    if species.kind == MoleculeKind.electron:
        return {}
    return entry_isotope_counts(entry, species)


def _transition_state_entry_isotope_reference(
    session: Session, transition_state_entry_id: int
) -> IsotopeCounts | None:
    """Sum the isotope counts of a TS entry's reactants.

    Mirrors :func:`_transition_state_entry_reference` exactly -- same reactants,
    same absences -- because a saddle point is made of every nucleus of the
    reacting system, isotopes included.
    """

    rows = _ts_entry_reactant_rows(session, transition_state_entry_id)
    if not rows or any(sp.kind == MoleculeKind.pseudo for sp, _ in rows):
        return None
    totals: IsotopeCounts = {}
    for species, entry in rows:
        if species.kind == MoleculeKind.electron:
            continue
        counts = entry_isotope_counts(entry, species)
        if counts is None:
            return None
        for key, n in counts.items():
            totals[key] = totals.get(key, 0) + n
    return totals


def _isotope_reference_for(
    calc: Calculation, session: Session
) -> tuple[str, IsotopeCounts] | None:
    if calc.species_entry_id is not None:
        owner_kind, owner_id = "species_entry", calc.species_entry_id
    elif calc.transition_state_entry_id is not None:
        owner_kind, owner_id = (
            "transition_state_entry",
            calc.transition_state_entry_id,
        )
    else:
        return None

    cache: dict[tuple[str, int], IsotopeCounts] = session.info.setdefault(
        _ISOTOPE_CACHE_KEY, {}
    )
    key = (owner_kind, owner_id)
    if key in cache:
        return owner_kind, cache[key]
    if owner_kind == "species_entry":
        reference = _species_entry_isotope_reference(session, owner_id)
    else:
        reference = _transition_state_entry_isotope_reference(session, owner_id)
    if reference is None:
        return None
    cache[key] = reference
    return owner_kind, reference


def _geometry_isotope_counts(session: Session, geometry_id: int) -> IsotopeCounts:
    """Count a stored geometry's non-standard isotopes.

    A ``D`` or ``T`` element spelling is an isotope declaration (#672, ADR
    0022): ``parse_xyz`` stores it as ``H`` with ``isotope_mass_number`` 2/3, so
    new rows are counted from the stored mass number. A row deposited before
    that decision holds ``D``/``T`` with a NULL mass number and cannot be
    rewritten (``trg_as_geometry_atom``), so the mass is read from the row's own
    symbol: ``mass = stored or implied_isotope_mass_number(element)``. Nothing is
    borrowed from a sibling record.
    """

    counts: IsotopeCounts = {}
    rows = session.execute(
        select(GeometryAtom.element, GeometryAtom.isotope_mass_number).where(
            GeometryAtom.geometry_id == geometry_id,
        )
    ).all()
    for element, stored_mass in rows:
        mass_number = (
            stored_mass if stored_mass is not None else implied_isotope_mass_number(element)
        )
        if mass_number is None:
            continue
        symbol = resolve_element_symbol(element)
        if normalize_isotope(symbol, mass_number) is None:
            continue
        key = (symbol, int(mass_number))
        counts[key] = counts.get(key, 0) + 1
    return counts


def assert_isotopes(
    session: Session,
    *,
    calc: Calculation,
    geometry_id: int,
    field: str,
) -> None:
    """Refuse a geometry whose isotopes disagree with the calculation's subject.

    Runs beside :func:`assert_calculation_geometry_composition` at every site
    that links a geometry to a calculation. That rule counts elements and reads
    ``D``/``T`` as hydrogen, so it cannot see that a deuterium geometry was
    attached to a protium species (or the reverse): a deuterium energy would be
    filed under a protium record.

    **Count-based, not atom-resolved.** The species entry carries an
    atom-resolved isotope key (``isotope_key``), but a calculation geometry has
    no atom map to the species graph, so there is nothing to align positions
    with. The comparison is therefore the multiset of ``(element,
    mass_number)`` substitutions, exactly as
    :func:`app.services.species_resolution.assert_geometry_isotopes_match_identity`
    does for conformer geometries. Consequence, stated plainly: isotopomers
    (CH2D-OH vs CH3-OD) are not distinguished -- a false acceptance, never a
    false refusal.

    The standard isotope is the empty mapping on both sides, so a geometry with
    no isotopes on a species with none is never touched.

    :raises CodedValueError: If the substitution multisets differ.
    """

    resolved = _isotope_reference_for(calc, session)
    if resolved is None:
        return
    owner_kind, reference = resolved

    observed = _geometry_isotope_counts(session, geometry_id)
    if observed == reference:
        return

    geometry_text = render_isotope_substitutions(observed)
    subject_text = render_isotope_substitutions(reference)
    subject = (
        "the species this calculation belongs to"
        if owner_kind == "species_entry"
        else "the reaction this transition state sits in"
    )

    logger.warning(
        "Calculation geometry isotope mismatch: calculation id=%s "
        "species_entry_id=%s transition_state_entry_id=%s geometry id=%s "
        "field=%s observed=%s reference=%s",
        calc.id,
        calc.species_entry_id,
        calc.transition_state_entry_id,
        geometry_id,
        field,
        geometry_text,
        subject_text,
    )

    repair = (
        "A deuterated or 13C-labelled geometry is a different molecule from "
        "the ordinary one: masses, frequencies and zero-point energy all "
        "differ. Declare the same substitution on both sides -- SMILES "
        "isotope notation (e.g. [2H]) on the species and geometry.isotopes "
        "on the structure -- or file the calculation under the isotopologue "
        "it was run on. Only the count of each substituted element is "
        "compared, so which atom carries the label is not checked."
    )

    # Built by concatenation, like the composition message: ``field`` is a
    # depositor-facing path, not a code, and a single f-string opening with
    # ``{field}: `` is the shape the catalogue gate reads as a code minted
    # from a parameter.
    raise CodedValueError(
        W_CALCULATION_GEOMETRY_ISOTOPE_MISMATCH,
        f"{field}: geometry isotopes are {geometry_text}, but {subject} "
        f"declares {subject_text} (calculation_geometry_isotope_mismatch). "
        + repair,
        context={
            "field": field,
            "owner_kind": owner_kind,
            "geometry_substitutions": geometry_text,
            "subject_substitutions": subject_text,
        },
        message_prefix=False,
    )
