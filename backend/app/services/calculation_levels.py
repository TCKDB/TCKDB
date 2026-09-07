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
  carries frequency results), and the energy (the linked ``sp``s' shared
  level when they agree; ``"ambiguous"`` when linked ``sp``s disagree;
  else an ``opt``'s; else ``composite``/``imported``). Never stored,
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
  code.
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
  at. It must equal the linked ``sp``s' shared level when any are linked,
  or every linked ``opt``'s level when none are (R5: opt-only is valid
  exactly when there is nothing to contradict).

Enforced by default on every write path that links role-tagged source
calculations -- there is no longer an ensemble-vs-standalone split, because
the ensemble-aware rules above are correct for both a single evidence chain
(a list of one) and a genuine multi-conformer bundle.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation
from app.db.models.level_of_theory import LevelOfTheory

#: Which role supplied the energy level -- shared by :class:`DerivedLevels`
#: and the read-schema field it feeds
#: (``app.schemas.reads.scientific_common.ScientificLevelsSummary
#: .energy_source``), so the two cannot silently drift apart on which
#: strings are valid. ``"ambiguous"``: linked ``sp``s exist but disagree
#: on level of theory, so no single energy level can be reported.
EnergySource = Literal["sp", "opt", "composite", "imported", "ambiguous"]

#: A record links two ``sp``s on the same optimisation's geometry (R2').
#: Distinct codes per product so a client branching on ``code`` never has
#: to know which product it asked about.
W_STATMECH_ROLE_DUPLICATE = "statmech_role_duplicate"
W_THERMO_ROLE_DUPLICATE = "thermo_role_duplicate"

#: A linked 'sp' calculation's input geometry is not an output geometry of
#: any linked 'opt' (R3').
W_STATMECH_SP_GEOMETRY_MISMATCH = "statmech_sp_geometry_mismatch"
W_THERMO_SP_GEOMETRY_MISMATCH = "thermo_sp_geometry_mismatch"

#: Either (a) some linked 'opt' has no covering 'sp' while at least one
#: other does (Coverage), or (b) a declared ``energy_level_of_theory``
#: differs from some linked 'opt's level and no 'sp' is linked at all
#: (R4'). Both name "an opt needs an sp (at this level) and does not have
#: one"; sharing the code keeps a client's remedy the same for either.
W_STATMECH_ENERGY_LEVEL_REQUIRES_SP = "statmech_energy_level_requires_sp"
W_THERMO_ENERGY_LEVEL_REQUIRES_SP = "thermo_energy_level_requires_sp"

#: A declared ``energy_level_of_theory`` disagrees with the linked 'sp'
#: set's own (shared) level of theory.
W_STATMECH_ENERGY_LEVEL_CONTRADICTION = "statmech_energy_level_contradiction"
W_THERMO_ENERGY_LEVEL_CONTRADICTION = "thermo_energy_level_contradiction"

#: Two or more linked 'sp's disagree on level of theory, so "the energy
#: level" has no single answer (R2').
W_STATMECH_ENERGY_LEVEL_AMBIGUOUS = "statmech_energy_level_ambiguous"
W_THERMO_ENERGY_LEVEL_AMBIGUOUS = "thermo_energy_level_ambiguous"


class RoleCalcInfo(NamedTuple):
    """The two facts :func:`derive_levels` needs about one role's calculation.

    Deliberately not the ``Calculation`` row itself: callers building this
    from bulk-loaded read-time data (thermo's per-record loop) often have
    no ORM row in hand, only a dict of scalar columns. Keeping this a
    plain tuple lets both the write-time (ORM-backed) and read-time
    (dict-backed) callers share one derivation function.
    """

    lot_id: int | None
    carries_frequencies: bool = False


class DerivedLevels(NamedTuple):
    """R1's answer: which ``level_of_theory.id`` governs each dimension."""

    geometry_lot_id: int | None
    frequency_lot_id: int | None
    energy_lot_id: int | None
    #: Which role supplied ``energy_lot_id``; ``"ambiguous"`` when linked
    #: ``sp``s disagreed (``energy_lot_id`` is then ``None``); ``None``
    #: when nothing linked can answer it.
    energy_source: EnergySource | None


def derive_levels(
    *,
    opts: Sequence[RoleCalcInfo] = (),
    freqs: Sequence[RoleCalcInfo] = (),
    sps: Sequence[RoleCalcInfo] = (),
    composites: Sequence[RoleCalcInfo] = (),
    importeds: Sequence[RoleCalcInfo] = (),
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
    element of ``sps`` is used -- to detect disagreement, not only to
    pick one.

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
    :returns: The derived levels. Any field may be ``None`` when nothing
        linked can answer that question.
    """
    opt = opts[0] if opts else None
    geometry_lot_id = opt.lot_id if opt is not None else None

    if freqs:
        frequency_lot_id = freqs[0].lot_id
    elif opt is not None and opt.carries_frequencies:
        frequency_lot_id = opt.lot_id
    else:
        frequency_lot_id = None

    energy_lot_id: int | None
    energy_source: EnergySource | None
    # ``None``-lot sps (an sp whose level of theory did not resolve) are
    # excluded from the disagreement count -- an unknown level is not
    # evidence of a *different* level, and every real sp still gets
    # counted once each.
    distinct_sp_lots = {i.lot_id for i in sps if i.lot_id is not None}
    if len(distinct_sp_lots) > 1:
        energy_lot_id, energy_source = None, "ambiguous"
    elif sps:
        energy_lot_id, energy_source = sps[0].lot_id, "sp"
    elif opt is not None:
        energy_lot_id, energy_source = opt.lot_id, "opt"
    elif composites:
        energy_lot_id, energy_source = composites[0].lot_id, "composite"
    elif importeds:
        energy_lot_id, energy_source = importeds[0].lot_id, "imported"
    else:
        energy_lot_id, energy_source = None, None

    return DerivedLevels(
        geometry_lot_id=geometry_lot_id,
        frequency_lot_id=frequency_lot_id,
        energy_lot_id=energy_lot_id,
        energy_source=energy_source,
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


def role_calc_infos(links: list[RoleLink], role: str) -> list[RoleCalcInfo]:
    """The ``RoleCalcInfo`` list :func:`derive_levels` wants for *role*.

    Exported so write-time callers (already holding ``RoleLink``s) can
    feed the same derivation function the read-time builders use,
    without duplicating the "carries frequencies" check.
    """
    return [
        RoleCalcInfo(
            lot_id=calc.lot_id,
            carries_frequencies=calc.freq_result is not None,
        )
        for calc in _by_role(links, role)
    ]


def _lot_label(lot: LevelOfTheory | None) -> str:
    """``method/basis`` (or bare ``method``), the way a chemist writes a LoT.

    Mirrors ``LevelOfTheorySummary.display`` -- for a refusal message, not
    a stored value.
    """
    if lot is None:
        return "unknown"
    return f"{lot.method}/{lot.basis}" if lot.basis else lot.method


def _match_sp_to_opts(
    sp: Calculation,
    opts: list[Calculation],
    opt_output_geoms: dict[int, set[int]],
) -> list[Calculation] | None:
    """Which linked ``opt``s *sp*'s geometry evidence covers (R3').

    Three outcomes:

    * ``None`` -- not comparable: no ``opt`` is linked at all, or
      geometry data is absent on the ``sp`` and/or on every ``opt``.
      Absence of evidence is never treated as a mismatch (house rule);
      the caller skips this ``sp`` entirely rather than counting or
      blaming it.
    * ``[]`` -- comparable data existed and disagreed: a genuine R3'
      violation. The caller raises.
    * non-empty list -- the ``opt``(s) this ``sp``'s geometry matches.
      With exactly one linked ``opt`` this is always that one once any
      data is absent on either side (there is no ambiguity about *which*
      opt to assume); with more than one, only ``opt``s whose declared
      output geometry the ``sp``'s declared input geometry intersects.
    """
    if not opts:
        return None
    sp_input_geoms = {row.geometry_id for row in sp.input_geometries}
    comparable_opts = [opt for opt in opts if opt_output_geoms[opt.id]]
    if not sp_input_geoms or not comparable_opts:
        return [opts[0]] if len(opts) == 1 else None
    return [
        opt for opt in comparable_opts if opt_output_geoms[opt.id] & sp_input_geoms
    ]


def assert_role_consistency(
    links: list[RoleLink],
    declared: LevelOfTheory | None,
    *,
    duplicate_code: str,
    geometry_mismatch_code: str,
    requires_sp_code: str,
    contradiction_code: str,
    ambiguous_code: str,
    subject: str,
) -> None:
    """R2'/R3'/Coverage/R4': the full ensemble-aware role-consistency check.

    One call replaces the four separate assertions this module used to
    expose, because the four questions share the same sp-to-opt geometry
    matching pass and answering them separately either recomputed it four
    times or forced a caller to thread the intermediate state through
    itself.

    **Precedence, when a deposit is wrong in more than one way at once**:
    R3' (a genuine geometry mismatch) first, then R2' distinctness (two
    sps on one opt's geometry), then R2' level uniformity (ambiguous),
    then Coverage, then R4'. A deposit that BOTH puts two sps on the same
    opt's geometry AND has those two sps disagree on level of theory
    (both are true of the same pair) is reported as the duplicate --
    ``*_role_duplicate``, not ``*_energy_level_ambiguous`` -- because
    distinctness is checked, and raised, first. This is not accidental:
    "two sps claim one optimisation" is the more specific fact and the
    one whose fix (remove the extra link) also fixes the level
    disagreement as a side effect, so it is the more useful first thing
    to tell a depositor.

    :param links: Every role link resolved for this upload (or bundle
        block) -- every linked ``opt``/``freq``/``sp``/``composite``/
        ``imported`` calculation, from every path that produced one.
    :param declared: The resolved ``energy_level_of_theory``, or ``None``
        when the depositor did not declare one (the field does not exist
        on every wire model this is called from -- see the module
        docstring).
    :param duplicate_code: Coded refusal for "two sp's on one opt's
        geometry" (R2').
    :param geometry_mismatch_code: Coded refusal for "an sp not on any
        linked opt's geometry" (R3').
    :param requires_sp_code: Coded refusal for "an opt has no covering sp
        while another does" (Coverage) and for "declared level differs
        from some opt's and no sp is linked at all" (R4').
    :param contradiction_code: Coded refusal for "declared level disagrees
        with the linked sp set's own (shared) level" (R4').
    :param ambiguous_code: Coded refusal for "linked sps disagree on level
        of theory" (R2').
    :param subject: ``"statmech"`` or ``"thermo"``, for messages.
    :raises CodedValueError: per the cases above.
    """
    opts = _by_role(links, "opt")
    sps = _by_role(links, "sp")

    opt_output_geoms = {
        opt.id: {row.geometry_id for row in opt.output_geometries} for opt in opts
    }
    opt_covering_sps: dict[int, list[Calculation]] = {opt.id: [] for opt in opts}

    for sp in sps:
        matched = _match_sp_to_opts(sp, opts, opt_output_geoms)
        if matched is None:
            continue
        if not matched:
            raise CodedValueError(
                geometry_mismatch_code,
                f"{subject}: the linked 'sp' calculation ({sp.public_ref}) "
                "was not run on a geometry any linked 'opt' calculation "
                "produced. Link the 'sp' role to a calculation whose "
                "input geometry is one of a linked optimisation's output "
                "geometries.",
                context={"sp_calculation_ref": sp.public_ref},
                message_prefix=False,
            )
        for opt in matched:
            opt_covering_sps[opt.id].append(sp)

    # R2' distinctness: no opt's geometry may be claimed by more than one sp.
    for opt in opts:
        covering = opt_covering_sps[opt.id]
        if len(covering) > 1:
            refs = [sp.public_ref for sp in covering]
            raise CodedValueError(
                duplicate_code,
                f"{subject}: {len(covering)} 'sp' links ({', '.join(refs)}) "
                f"claim the same optimisation's geometry ({opt.public_ref}), "
                f"but a {subject} record may have at most one 'sp' per "
                "optimisation. Remove the extra link.",
                context={
                    "opt_calculation_ref": opt.public_ref,
                    "sp_calculation_refs": refs,
                },
                message_prefix=False,
            )

    # R2' level-of-theory uniformity across every linked sp. An sp with no
    # resolved level of theory is excluded from the disagreement count --
    # see the identical exclusion in :func:`derive_levels`.
    distinct_sp_lot_ids = {sp.lot_id for sp in sps if sp.lot_id is not None}
    if len(distinct_sp_lot_ids) > 1:
        refs = [sp.public_ref for sp in sps]
        raise CodedValueError(
            ambiguous_code,
            f"{subject}: the linked 'sp' calculations ({', '.join(refs)}) "
            "run at more than one level of theory, so this record's "
            "energy level has no single answer. Link every 'sp' at the "
            "same level, or split this record so each level gets its own.",
            context={"sp_calculation_refs": refs},
            message_prefix=False,
        )

    # Coverage: any sp at all obliges every opt to have one. Unconditional
    # -- this is the "forgot the SP for one conformer" deposit, and it is
    # exactly as wrong whether or not a level was ever declared.
    if sps:
        uncovered = [opt for opt in opts if not opt_covering_sps[opt.id]]
        if uncovered:
            refs = [opt.public_ref for opt in uncovered]
            noun = "optimisation" if len(refs) == 1 else "optimisations"
            verb = "has" if len(refs) == 1 else "have"
            pronoun = "its" if len(refs) == 1 else "their"
            sp_refs = [sp.public_ref for sp in sps]
            if len(uncovered) == len(opts):
                # No sp could be matched to *any* linked optimisation's
                # output geometry at all -- not "one conformer forgot its
                # sp", but "none of the linked sps carry geometry evidence
                # that reaches any linked opt". A different fact, so a
                # different sentence: naming "at least one other
                # optimisation... does" would be false here.
                raise CodedValueError(
                    requires_sp_code,
                    f"{subject}: {len(refs)} {noun} ({', '.join(refs)}) "
                    f"{verb} no 'sp' calculation linked at {pronoun} geometry, "
                    "and none of the linked 'sp' calculations "
                    f"({', '.join(sp_refs)}) could be matched to any "
                    "linked optimisation's output geometry. Link an 'sp' "
                    "whose input geometry is one of these optimisations' "
                    "output geometries, or declare no 'sp' at all.",
                    context={
                        "uncovered_opt_calculation_refs": refs,
                        "sp_calculation_refs": sp_refs,
                    },
                    message_prefix=False,
                )
            raise CodedValueError(
                requires_sp_code,
                f"{subject}: {len(refs)} {noun} ({', '.join(refs)}) "
                f"{verb} no 'sp' calculation linked at {pronoun} geometry, but "
                "at least one other optimisation in this record does. "
                "Link an 'sp' for every optimisation this record's "
                "energy claims, or none.",
                context={"uncovered_opt_calculation_refs": refs},
                message_prefix=False,
            )

    if declared is None:
        return

    if sps:
        sp_lot_id = next(iter(distinct_sp_lot_ids), None)
        if sp_lot_id != declared.id:
            sp = sps[0]
            raise CodedValueError(
                contradiction_code,
                f"{subject}: the declared energy level of theory "
                f"({_lot_label(declared)}) does not match the linked 'sp' "
                f"calculations' level ({_lot_label(sp.lot)}). Declare the "
                "level the linked single points actually ran at, or link "
                "'sp' calculations run at the declared level.",
                context={
                    "declared_level_of_theory_ref": declared.public_ref,
                    "sp_calculation_refs": [s.public_ref for s in sps],
                    "sp_level_of_theory_ref": (
                        sp.lot.public_ref if sp.lot is not None else None
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


__all__ = [
    "W_STATMECH_ENERGY_LEVEL_AMBIGUOUS",
    "W_STATMECH_ENERGY_LEVEL_CONTRADICTION",
    "W_STATMECH_ENERGY_LEVEL_REQUIRES_SP",
    "W_STATMECH_ROLE_DUPLICATE",
    "W_STATMECH_SP_GEOMETRY_MISMATCH",
    "W_THERMO_ENERGY_LEVEL_AMBIGUOUS",
    "W_THERMO_ENERGY_LEVEL_CONTRADICTION",
    "W_THERMO_ENERGY_LEVEL_REQUIRES_SP",
    "W_THERMO_ROLE_DUPLICATE",
    "W_THERMO_SP_GEOMETRY_MISMATCH",
    "DerivedLevels",
    "RoleCalcInfo",
    "RoleLink",
    "assert_role_consistency",
    "derive_levels",
    "role_calc_infos",
]
