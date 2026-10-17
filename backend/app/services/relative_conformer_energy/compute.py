"""The pure relative-energy computation: level filter, certificate, cohort, repeats, reference, differences.

Pure. It reads no database, so a recorded answer replays from its manifest alone. Input is the compared groups, the
conformer-basin determinations of them with their assessments (made by the structure assessment, unchanged) and the
request; output is a :class:`RelativeEnergyResult` over public refs.

What is decided here, and nowhere else:

* **A value belongs to the member its own record names.** A determination counts for the conformer group its
  observation belongs to, and for no other. Nothing is borrowed from a sibling group, and a higher-level single
  point never inherits a lower-level minimum certificate: the certificate says which surface it was judged on.
* **One level, one cohort.** Only a value whose energy calculation is at the requested level counts. Among those,
  values in more than one recipe cohort are not mixed: the answer lists the cohorts and computes nothing.
* **Repeats are alternates of one member.** Several determinations of one group that agree give one value; ones that
  disagree are reported as disagreeing (``retain_alternates``) or the first in the administrative order stands, and
  is labelled, under ``administrative_representative``. The lower value is never picked for being lower.
* **A contradiction survives a pass.** A basin with a qualifying and a refuting determination is contested and has no
  value (the structure decision's own rule, through the same predicates).
* **The reference is named, never chosen.** Differences are ``value - reference value`` in hartree, converted once to
  kJ/mol. A reference without a single value gives no difference for anyone; the reference is never replaced.
* **Missing is missing.** A member without a usable value is ``absent`` with a coded reason (``unresolved`` when it has
  values that cannot be reduced to one). It is never zero, and it never takes part in a difference.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.chemistry.units import HARTREE_TO_KJ_MOL
from app.services.relative_conformer_energy.models import (
    BASIS_INCOMPLETE,
    BASIS_MULTIPLE_RECIPES,
    BASIS_NO_MEMBERS,
    BASIS_REFERENCE_ONLY,
    BASIS_REFERENCE_UNAVAILABLE,
    BASIS_RELATIVE,
    BASIS_SURFACE_NOTE,
    CERTIFICATE_NOT_ON_ENERGY_SURFACE,
    CERTIFICATE_NOT_SUPPORTED,
    CONTESTED_CERTIFICATE,
    DETERMINATION_BELOW_REVIEW_FLOOR,
    DETERMINATION_TERMINAL_REVIEW_STATUS,
    LEVEL_BOUND_TO_COMPOSITE_SCHEME,
    MULTIPLE_RECIPES_AT_LEVEL,
    NO_DETERMINATION_SUPPLIES_KIND,
    NO_STRUCTURE_DETERMINATION,
    NO_VALUE_AT_LEVEL,
    NOT_ELIGIBLE,
    NOTE_ORDER,
    RECIPE_DIFFERS,
    RECIPE_NOT_ESTABLISHED,
    REFERENCE_UNAVAILABLE_DETAIL,
    REPEAT_VALUES_DISAGREE,
    Alternate,
    Certificate,
    MemberCertificate,
    MemberResult,
    MemberStatus,
    RelativeEnergyOutcome,
    RelativeEnergyRequest,
    RelativeEnergyResult,
)
from app.services.structure_selection.decision import (
    BASIS_REPRESENTATIVE,
    build_units,
    qualifies,
    refutes,
    target_key,
)
from app.services.structure_selection.models import (
    AdminPolicy,
    CoverageRequirement,
    LevelFacts,
    NormalizedCalculation,
    NormalizedDetermination,
    RepeatPolicy,
    StructureAssessment,
    StructureRequest,
)

if TYPE_CHECKING:
    # The decision's per-unit view; named for annotations only (``build_units`` returns it).
    from app.services.structure_selection.decision import _U

# How near a unit came to supplying a value, best first. A member with no value reports the reason of its closest unit.
_RANK_OTHER_LEVEL = 0
_RANK_SURFACE = 1
_RANK_NO_COHORT = 2
_RANK_RECIPE_DIFFERS = 3
_RANK_RECIPE_UNESTABLISHED = 4
_RANK_CERTIFICATE = 5
_RANK_KIND = 6
_RANK_OTHER = 7

_KIND_CODES = frozenset({"determination_supplies_other_quantity", "record_does_not_supply_requested_quantity"})


def _level_relation(level: LevelFacts, request: RelativeEnergyRequest) -> str:
    """``match``, ``other`` or ``bound`` (the requested lot is the one a composite scheme is bound to)."""
    selector = request.level
    if selector.composite_scheme_ref is not None:
        return "match" if level.composite_scheme_ref == selector.composite_scheme_ref else "other"
    if level.level_ref != selector.level_of_theory_ref:
        return "other"
    return "match" if level.composite_scheme_ref is None else "bound"


def _on_energy_surface(a: StructureAssessment, energy_level_ref: str | None) -> bool:
    return (
        a.claim is not None
        and energy_level_ref is not None
        and a.claim.surface_level_refs == (energy_level_ref,)
    )


def _ineligible(a: StructureAssessment) -> tuple[int, str, tuple[str, ...]]:
    codes = sorted({r.code for r in a.reasons} | set(a.blocking))
    if _KIND_CODES & set(codes):
        return _RANK_KIND, NO_DETERMINATION_SUPPLIES_KIND, ()
    if any(c.startswith("recipe_fact_differs") for c in codes):
        return _RANK_RECIPE_DIFFERS, RECIPE_DIFFERS, ()
    if any(c.startswith(("recipe_fact_unestablished_for_request", "recipe_conflict")) for c in codes):
        return _RANK_RECIPE_UNESTABLISHED, RECIPE_NOT_ESTABLISHED, ()
    if a.claim is None or not a.claim.supported or "curvature_contradicts_claim" in codes:
        return _RANK_CERTIFICATE, CERTIFICATE_NOT_SUPPORTED, tuple(codes)
    return _RANK_OTHER, NOT_ELIGIBLE, tuple(codes)


class _Judged:
    """One unit judged against the request: usable (``ok``) or the reason it is not."""

    __slots__ = ("detail", "ok", "rank", "reason", "unit")

    def __init__(self, unit: "_U", *, ok: bool, rank: int = _RANK_OTHER, reason: str = NOT_ELIGIBLE, detail: tuple[str, ...] = ()):
        self.unit, self.ok, self.rank, self.reason, self.detail = unit, ok, rank, reason, detail


def _judge(
    u: "_U", request: RelativeEnergyRequest, structure_request: StructureRequest, calculations: dict[str, NormalizedCalculation]
) -> _Judged:
    a = u.assessment
    if not qualifies(structure_request, u):
        rank, reason, detail = _ineligible(a)
        return _Judged(u, ok=False, rank=rank, reason=reason, detail=detail)
    assert a.energy is not None
    energy_calc = calculations.get(a.energy.calculation_ref)
    if energy_calc is None:
        return _Judged(u, ok=False, rank=_RANK_OTHER, reason=NOT_ELIGIBLE, detail=("energy_calculation_not_loaded",))
    relation = _level_relation(energy_calc.level, request)
    if relation != "match":
        detail = (LEVEL_BOUND_TO_COMPOSITE_SCHEME,) if relation == "bound" else ()
        return _Judged(u, ok=False, rank=_RANK_OTHER_LEVEL, reason=NO_VALUE_AT_LEVEL, detail=detail)
    if request.certificate is Certificate.local_minimum_on_energy_surface and not _on_energy_surface(
        a, energy_calc.level.level_ref
    ):
        return _Judged(u, ok=False, rank=_RANK_SURFACE, reason=CERTIFICATE_NOT_ON_ENERGY_SURFACE)
    if u.cohort is None:
        return _Judged(u, ok=False, rank=_RANK_NO_COHORT, reason=RECIPE_NOT_ESTABLISHED)
    return _Judged(u, ok=True)


def _absent(ref: str, reason: str, detail: tuple[str, ...] = ()) -> MemberResult:
    return MemberResult(conformer_ref=ref, status=MemberStatus.absent, absent_reason=reason, detail=detail)


def _certificate(units: Sequence["_U"], chosen: "_U", calculations: dict[str, NormalizedCalculation]) -> MemberCertificate:
    claim = chosen.assessment.claim
    assert claim is not None
    surfaces = sorted({s for u in units if u.assessment.claim is not None for s in u.assessment.claim.surface_level_refs})
    witnesses = sorted({w for u in units if u.assessment.claim is not None for w in u.assessment.claim.witness_refs})

    def on_surface(u: "_U") -> bool:
        assert u.assessment.energy is not None
        calc = calculations[u.assessment.energy.calculation_ref]
        return _on_energy_surface(u.assessment, calc.level.level_ref)

    return MemberCertificate(
        claim=claim.claim,
        supported=claim.supported,
        surface_level_refs=tuple(surfaces),
        on_energy_surface=all(on_surface(u) for u in units),
        witness_refs=tuple(witnesses),
    )


def _value_geometry(ordered: Sequence["_U"], hartree: float) -> str | None:
    """The one geometry every record giving ``hartree`` was evaluated at, or ``None`` (none recorded, or several)."""
    refs = {u.assessment.energy.geometry_ref for u in ordered if u.assessment.energy and u.assessment.energy.hartree == hartree}
    return next(iter(refs)) if len(refs) == 1 and None not in refs else None


def _member_from_values(
    ref: str,
    units: list["_U"],
    request: RelativeEnergyRequest,
    calculations: dict[str, NormalizedCalculation],
) -> MemberResult:
    """One group's member from its usable units (all in the one cohort), in administrative order."""
    ordered = sorted(units, key=lambda u: (u.admin_key, u.node.id_rank))
    values = sorted({u.assessment.energy.hartree for u in ordered if u.assessment.energy is not None})
    determination_refs = tuple(sorted(u.ref for u in ordered))
    if len(values) > 1 and request.repeat_policy is RepeatPolicy.retain_alternates:
        return MemberResult(
            conformer_ref=ref,
            status=MemberStatus.unresolved,
            determination_refs=determination_refs,
            alternates=tuple(
                Alternate(u.ref, u.assessment.energy.hartree) for u in ordered if u.assessment.energy is not None
            ),
            absent_reason=REPEAT_VALUES_DISAGREE,
        )
    chosen = ordered[0]
    energy = chosen.assessment.energy
    assert energy is not None
    differing = len(values) > 1
    return MemberResult(
        conformer_ref=ref,
        status=MemberStatus.present,
        value_hartree=energy.hartree,
        value_uncertainty_hartree=energy.uncertainty_hartree,
        representative=differing,
        determination_refs=determination_refs,
        energy_calculation_refs=tuple(sorted({u.assessment.energy.calculation_ref for u in ordered if u.assessment.energy})),
        evaluated_geometry_refs=tuple(
            sorted({u.assessment.energy.geometry_ref for u in ordered if u.assessment.energy and u.assessment.energy.geometry_ref})
        ),
        value_geometry_ref=_value_geometry(ordered, energy.hartree),
        certificate=_certificate(ordered, chosen, calculations),
        alternates=tuple(
            Alternate(u.ref, u.assessment.energy.hartree)
            for u in ordered
            if u.assessment.energy is not None and u.assessment.energy.hartree != energy.hartree
        ),
    )


def _no_unit_reason(group: str, floor_held: frozenset[str], terminal_held: frozenset[str]) -> str:
    if group in floor_held:
        return DETERMINATION_BELOW_REVIEW_FLOOR
    if group in terminal_held:
        return DETERMINATION_TERMINAL_REVIEW_STATUS
    return NO_STRUCTURE_DETERMINATION


def _without_value(ref: str, judged: list[_Judged]) -> MemberResult:
    best = min(judged, key=lambda j: j.rank)
    return _absent(ref, best.reason, best.detail)


def compute_relative_energies(
    request: RelativeEnergyRequest,
    structure_request: StructureRequest,
    *,
    group_refs: Sequence[str],
    excluded_groups: Mapping[str, str] | None = None,
    floor_excluded_refs: frozenset[str] = frozenset(),
    terminal_excluded_refs: frozenset[str] = frozenset(),
    determinations: Sequence[NormalizedDetermination],
    assessments: Sequence[StructureAssessment],
    calculations: dict[str, NormalizedCalculation],
) -> RelativeEnergyResult:
    """The relative energies of every compared group against the named reference. Pure; see the module docstring.

    :param group_refs: The compared conformer groups (every one the read profile shows, reference included).
    :param excluded_groups: Compared groups their own review state excludes (group to reason code); members, reported
        absent, never assessed.
    :param floor_excluded_refs: Groups with a determination the caller's floor holds out; reported with a floor reason
        when they have no other value.
    :param determinations: The conformer-basin determinations the assessment was run over.
    :param assessments: One assessment per determination. Replay passes freshly *recomputed* ones.
    :param calculations: Every loaded calculation by public ref (a determination's source calculations).
    """
    groups = tuple(sorted(group_refs))
    excluded: Mapping[str, str] = excluded_groups or {}
    reference = request.reference_conformer_ref
    if not groups:
        return RelativeEnergyResult(
            outcome=RelativeEnergyOutcome.no_members,
            basis=BASIS_NO_MEMBERS,
            energy_kind=request.energy_kind,
            level=request.level,
            recipe_cohort_id=None,
            recipe_facts=(),
            reference_ref=reference,
            members=(),
            coverage=_coverage(request, ()),
            notes=(NOTE_ORDER,),
        )
    if reference not in groups:
        raise ValueError("the named reference conformer is not among the compared conformer groups")

    built = build_units(determinations, assessments, calculations, AdminPolicy.default)
    by_group: dict[str, list["_U"]] = defaultdict(list)
    for u in built:
        group = u.unit.conformer_group_ref if isinstance(u.unit, NormalizedDetermination) else None
        if group is not None and group in groups and group not in excluded:
            by_group[group].append(u)

    members: dict[str, MemberResult] = {}
    usable: dict[str, list[_Judged]] = {}
    judged_by_group: dict[str, list[_Judged]] = {}
    for g in groups:
        if g in excluded:
            members[g] = _absent(g, excluded[g])
            continue
        units = by_group.get(g, [])
        if not units:
            members[g] = _absent(g, _no_unit_reason(g, floor_excluded_refs, terminal_excluded_refs))
            continue
        # A target (one observation's basin) with a qualifying and a refuted determination is contested: the pass is
        # not an answer, and nothing of that group is used.
        by_target: dict[str, list["_U"]] = defaultdict(list)
        for u in units:
            by_target[target_key(u.unit)].append(u)
        contested = sorted(
            t
            for t, us in by_target.items()
            if any(qualifies(structure_request, u) for u in us) and any(refutes(u.assessment) for u in us)
        )
        if contested:
            members[g] = _absent(g, CONTESTED_CERTIFICATE)
            continue
        judged = [_judge(u, request, structure_request, calculations) for u in units]
        judged_by_group[g] = judged
        ok = [j for j in judged if j.ok]
        if ok:
            usable[g] = ok

    cohorts: dict[str, list["_U"]] = defaultdict(list)
    for js in usable.values():
        for j in js:
            assert j.unit.cohort is not None
            cohorts[j.unit.cohort].append(j.unit)

    unresolved: list[dict[str, Any]] = []
    notes: list[str] = [NOTE_ORDER]
    cohort_entries: tuple[dict[str, Any], ...] = tuple(
        {
            "cohort_id": cid,
            "recipe_facts": [f.to_dict() for f in (us[0].assessment.recipe.facts if us[0].assessment.recipe else ())],
            "member_refs": sorted(
                {u.unit.conformer_group_ref for u in us if isinstance(u.unit, NormalizedDetermination) and u.unit.conformer_group_ref}
            ),
            "unit_refs": sorted(u.ref for u in us),
        }
        for cid, us in sorted(cohorts.items())
    )
    multiple = len(cohorts) > 1
    if multiple:
        unresolved.append({"code": MULTIPLE_RECIPES_AT_LEVEL, "refs": sorted(cohorts)})

    for g in groups:
        if g in members:
            continue
        if g in usable and not multiple:
            members[g] = _member_from_values(g, [j.unit for j in usable[g]], request, calculations)
        elif g in usable:
            members[g] = MemberResult(
                conformer_ref=g,
                status=MemberStatus.unresolved,
                determination_refs=tuple(sorted(j.unit.ref for j in usable[g])),
                absent_reason=MULTIPLE_RECIPES_AT_LEVEL,
                detail=tuple(sorted({j.unit.cohort for j in usable[g] if j.unit.cohort})),
            )
        else:
            members[g] = _without_value(g, judged_by_group[g])

    disagreeing = sorted(g for g, m in members.items() if m.absent_reason == REPEAT_VALUES_DISAGREE)
    if disagreeing:
        unresolved.append({"code": REPEAT_VALUES_DISAGREE, "refs": disagreeing})
    if any(m.representative for m in members.values()):
        notes.append(BASIS_REPRESENTATIVE)

    # --- the reference and the differences ---------------------------------------------------------------------
    ref_member = members[reference]
    ref_value = ref_member.value_hartree
    final: list[MemberResult] = []
    for g in groups:
        m = members[g]
        if g == reference and m.has_value and ref_value is not None:
            final.append(replace(m, status=MemberStatus.reference, relative_energy_hartree=0.0, relative_energy_kj_mol=0.0))
        elif m.has_value and ref_value is not None and m.value_hartree is not None:
            delta = m.value_hartree - ref_value
            final.append(replace(m, relative_energy_hartree=delta, relative_energy_kj_mol=delta * HARTREE_TO_KJ_MOL))
        elif m.has_value:
            final.append(replace(m, detail=(*m.detail, REFERENCE_UNAVAILABLE_DETAIL)))
        else:
            final.append(m)

    reference_has_value = ref_value is not None
    others_present = any(m.has_value for m in final if m.conformer_ref != reference)
    coverage = _coverage(request, tuple(final))
    if multiple:
        outcome, basis = RelativeEnergyOutcome.multiple_recipes_at_level, BASIS_MULTIPLE_RECIPES
    elif not reference_has_value:
        outcome, basis = RelativeEnergyOutcome.reference_unavailable, BASIS_REFERENCE_UNAVAILABLE
    elif request.coverage is CoverageRequirement.all_requested_members and not coverage["complete"]:
        outcome, basis = RelativeEnergyOutcome.incomplete_coverage, BASIS_INCOMPLETE
    elif others_present:
        outcome, basis = RelativeEnergyOutcome.relative_energies, BASIS_RELATIVE
    else:
        outcome, basis = RelativeEnergyOutcome.reference_only, BASIS_REFERENCE_ONLY
    if any(m.certificate is not None and not m.certificate.on_energy_surface for m in final if m.has_value):
        basis = f"{basis}; {BASIS_SURFACE_NOTE}"

    single = next(iter(cohort_entries), None) if len(cohort_entries) == 1 else None
    return RelativeEnergyResult(
        outcome=outcome,
        basis=basis,
        energy_kind=request.energy_kind,
        level=request.level,
        recipe_cohort_id=single["cohort_id"] if single else None,
        recipe_facts=tuple(single["recipe_facts"]) if single else (),
        reference_ref=reference,
        members=tuple(final),
        coverage=coverage,
        unresolved=tuple(unresolved),
        cohorts=cohort_entries,
        notes=tuple(notes),
    )


def _coverage(request: RelativeEnergyRequest, members: tuple[MemberResult, ...]) -> dict[str, Any]:
    present = sum(1 for m in members if m.has_value)
    absent = sum(1 for m in members if m.status is MemberStatus.absent)
    unresolved = sum(1 for m in members if m.status is MemberStatus.unresolved)
    reference = next((m for m in members if m.conformer_ref == request.reference_conformer_ref), None)
    return {
        "requirement": request.coverage.value,
        "requested_members": len(members),
        "present": present,
        "absent": absent,
        "unresolved": unresolved,
        "complete": bool(members) and absent == 0 and unresolved == 0 and reference is not None and reference.has_value,
        "authorized_population_only": True,
    }
