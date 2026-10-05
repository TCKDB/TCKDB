"""The structure decision: cohorts, repeats, numerical order and protocol preference over assessed units.

Pure. It reads no database, so a recorded decision replays from its manifest alone. Input is every assessed unit
(with its assessment) and the request; output is a :class:`StructureDecision` over public refs.

What is specific to structures, and lives here and not in the shared kernel:

* **A cohort is the unit of numerical comparison.** Units are ordered by value only inside one cohort: the same
  established actual recipe, the same quantity and energy convention, and the same scope family (an unconverged
  optimisation's intermediate-geometry value does not mix with converged values). Different cohorts may each return
  a conditional minimum; they never produce one overall winner by absolute total energy, because two methods'
  total energies are not on one scale. Several cohorts therefore end as ``incomparable_alternatives`` with each
  cohort's own minimum listed.
* **A number never decides a method.** Numerical order finds the lowest *value* in a justified cohort. Which
  *protocol* is preferred is a different question and is answered only by audited rules through the shared graph
  kernel; with none active every protocol stays unranked.
* **Exact ties are all kept.** There is no tolerance and no "near" chain: values compare exactly, ties keep every
  tied reference, and the administrative key only orders what is already tied or already in one scientific front.
* **Repeats are retained.** Several determinations of one target (one basin, geometry or saddle) are alternates
  and not independent confirmation. If their values differ exactly, the target has no single value, so its cohort
  cannot be ordered, unless the request names the administrative-representative policy, in which case the first
  repeat in the administrative order stands for the target and the decision says so (it is never the minimum of
  all stored values). Nothing infers that two differently keyed determinations are repeats: unknown relationships
  neither add degeneracy nor merge conflicts.
* **A contradiction survives a pass.** If one determination of a target is eligible and another determination of
  the same target is refuted (the stored curvature contradicts the claim, or an applicable finding invalidates it),
  the target is contested and none of it certifies. Choosing the older pass is not an answer. Adjudicating
  curvature evidence is not modelled in version 1.
* **Coverage is what the caller asked for.** ``known_values`` orders what is known, conditional on it.
  ``all_requested_members`` is a complete claim: every visible requested member must be eligible and in one
  cohort. Even then the claim is about the authorized population the caller could see, not a search certificate.

The administrative key (versioned, echoed with every decision) is: review rank ascending, permitted-quality rank
ascending, creation time descending, stable ordinal descending; ``latest`` is time and ordinal descending only,
``earliest`` time and ordinal ascending. It sorts within a scientific front or among exact ties and nothing else.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import REVIEW_RANK, SelectionPolicy
from app.services.selection_kernel import (
    ADMIN_FIRST_BASIS,
    AdminNode,
    Edge,
    Outcome,
    Tri,
    decide_graph,
)
from app.services.structure_selection.models import (
    AdminPolicy,
    CoverageRequirement,
    EnergyScope,
    Grain,
    Intent,
    NormalizedCalculation,
    NormalizedDetermination,
    NormalizedRecipe,
    RepeatPolicy,
    ResultMode,
    StructureAssessment,
    StructureOutcome,
    StructureRequest,
    StructureSubject,
)
from app.services.structure_selection.rules import (
    EDGE_LABEL,
    RULE_ACTIVE,
    ProtocolCandidate,
    StructureRule,
    validate_rules,
)

#: Version of the decision semantics (cohorts, repeats, ordering, outcome selection).
DECISION_VERSION = "1"
#: Version of the administrative key below.
ADMIN_KEY_VERSION = "1"

ADMIN_KEY_SPEC: dict[str, Any] = {
    "version": ADMIN_KEY_VERSION,
    "default": ["review_rank ascending", "quality_rank ascending", "created_at descending", "ordinal descending"],
    "latest": ["created_at descending", "ordinal descending"],
    "earliest": ["created_at ascending", "ordinal ascending"],
}

_QUALITY_RANK = {"curated": 0, "raw": 1, "rejected": 2}
_UNKNOWN_QUALITY_RANK = 3
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_ENERGY_UNAVAILABLE_CODES = frozenset(
    {"energy_not_deposited", "energy_not_stated", "energy_source_not_declared", "e0_convention_not_stated"}
)
_FINDING_BLOCK_PREFIXES = ("finding_invalidates", "role_invalidated")

BASIS_NO_CANDIDATES = "no authorized unit matches this target; nothing was assessed"
BASIS_ENERGY_UNAVAILABLE = "matching records exist but none states a usable value of the requested quantity"
BASIS_NONE_APPLICABLE = "no assessed unit qualifies for this request"
BASIS_EVIDENCE_CONFLICT = (
    "an applicable finding or contradicting characterization prevents the requested certification; "
    "contradictions are retained, not outvoted by an older pass"
)
BASIS_UNRESOLVED = (
    "the cohort or linkage meaning needed to compare these units is not established; they are reported, not ordered"
)
BASIS_RECORDED_MINIMUM = (
    "lowest known eligible recorded value within one justified cohort; conditional on the values deposited, "
    "not a global minimum and not a validated structure"
)
BASIS_VALIDATED_MINIMUM = (
    "lowest comparable energy among the validated, completely assessed requested members of one cohort; "
    "a statement about the authorized population, never a global-search or conformational-search certificate"
)
BASIS_QUALIFIED = (
    "the stated structural claim is supported for each listed determination; this is not a numerical minimum "
    "and not a rate claim"
)
BASIS_CROSS_COHORT = (
    "the eligible units fall in more than one cohort; each cohort's own minimum is listed, but total energies of "
    "different protocols are not on one scale, so there is no overall winner"
)
BASIS_REPRESENTATIVE = (
    "each target is represented by its first determination in the administrative order; that representative is "
    "an administrative choice and not the minimum of all stored values"
)
BASIS_REPRESENTATIVE_MINIMUM = (
    "lowest value among each target's administrative representative within one cohort; this is NOT the minimum of all "
    "stored values: a target's other determinations (listed per target as alternates) may be lower, and which one stands "
    "for the target is an administrative choice, not a scientific one"
)
BASIS_CROSS_COHORT_PARTIAL = (
    "more than one cohort is established; the cohort minima that could be ordered are listed, and any cohort that could "
    "not be ordered is listed with its reason. A single cohort is never named the winner because another could not be "
    "ordered: total energies of different protocols are not on one scale, and added uncertainty never makes a claim stronger"
)
SEARCH_COMPLETENESS = (
    "coverage is the caller's authorized population only; it proves neither a corpus-wide best nor an exhaustive "
    "conformational search"
)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def _micros(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment - _EPOCH) // timedelta(microseconds=1)


def target_key(unit: NormalizedCalculation | NormalizedDetermination) -> str:
    """What a unit is a claim about, by public refs only.

    A calculation is its own target. A basin determination is about its conformer observation, a geometry
    determination about its evaluated geometry, a saddle about its entry and evaluated geometry. Two determinations
    share a target only when this key is equal; nothing else is inferred to relate them.
    """
    if isinstance(unit, NormalizedCalculation):
        return f"calculation:{unit.calculation_ref}"
    if unit.target_kind == "conformer_basin":
        return f"basin:{unit.conformer_observation_ref}"
    if unit.target_kind == "saddle_point":
        return f"saddle:{unit.owner_ref}:{unit.evaluated_geometry_ref}"
    return f"geometry:{unit.evaluated_geometry_ref}"


def unit_ref(unit: NormalizedCalculation | NormalizedDetermination) -> str:
    return unit.calculation_ref if isinstance(unit, NormalizedCalculation) else unit.determination_ref


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def cohort_id(recipe: NormalizedRecipe | None, assessment: StructureAssessment, convention: Any) -> str | None:
    """The cohort a unit's value can be ordered in, or ``None`` when its recipe does not establish one.

    The cohort is the recipe's established key, the quantity, the energy convention (for a zero-kelvin energy: how
    the zero-point and corrections were assembled) and whether the value is an unconverged intermediate-geometry
    one. Software identity does not divide a cohort in normaliser version 1; it is disclosed on the unit.
    """
    if recipe is None or recipe.cohort_key is None or assessment.energy is None:
        return None
    payload = {
        "recipe": recipe.cohort_key,
        "quantity": assessment.energy.quantity,
        "convention": convention,
        "intermediate_geometry": assessment.energy.scope is EnergyScope.unconverged_endpoint,
    }
    return "coh_" + hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class _U:
    """One unit as the decision sees it."""

    ref: str
    unit: NormalizedCalculation | NormalizedDetermination
    assessment: StructureAssessment
    target: str
    cohort: str | None
    admin_key: tuple
    node: AdminNode

    @property
    def eligible_base(self) -> bool:
        return self.assessment.physically_eligible


def _quality_rank(refs: Sequence[str], calcs: dict[str, NormalizedCalculation]) -> int:
    ranks = [_QUALITY_RANK.get(calcs[r].quality, _UNKNOWN_QUALITY_RANK) for r in refs if r in calcs]
    return max(ranks) if ranks and len(ranks) == len(refs) else _UNKNOWN_QUALITY_RANK


def _relevant_calc_refs(unit: NormalizedCalculation | NormalizedDetermination, a: StructureAssessment) -> list[str]:
    if isinstance(unit, NormalizedCalculation):
        return [unit.calculation_ref]
    refs: list[str] = []
    if a.energy is not None:
        refs.append(a.energy.calculation_ref)
    if a.claim is not None:
        refs.extend(r for r in a.claim.witness_refs if r not in refs)
    return refs


def admin_key(
    policy: AdminPolicy, *, review: RecordReviewStatus, quality_rank: int, created_at: datetime, ordinal: int
) -> tuple:
    """The versioned administrative key (see :data:`ADMIN_KEY_SPEC`); direction is already baked in."""
    micros = _micros(created_at)
    if policy is AdminPolicy.latest:
        return (-micros, -ordinal)
    if policy is AdminPolicy.earliest:
        return (micros, ordinal)
    return (REVIEW_RANK[review], quality_rank, -micros, -ordinal)


def _convention(unit: NormalizedCalculation | NormalizedDetermination) -> Any:
    if isinstance(unit, NormalizedDetermination):
        return unit.energy_convention
    return unit.energy.composite_assembly


def build_units(
    units: Sequence[NormalizedCalculation | NormalizedDetermination],
    assessments: Sequence[StructureAssessment],
    calculations: dict[str, NormalizedCalculation],
    policy: AdminPolicy,
) -> list[_U]:
    by_ref = {a.unit_ref: a for a in assessments}
    built: list[_U] = []
    for unit in units:
        ref = unit_ref(unit)
        a = by_ref[ref]
        key = admin_key(
            policy,
            review=unit.review_status,
            quality_rank=_quality_rank(_relevant_calc_refs(unit, a), calculations),
            created_at=unit.created_at,
            ordinal=unit.id_rank,
        )
        node = AdminNode(
            ref=ref, id_rank=unit.id_rank, review_status=unit.review_status, created_at=unit.created_at, admin_key=key
        )
        built.append(_U(ref, unit, a, target_key(unit), cohort_id(a.recipe, a, _convention(unit)), key, node))
    return built


def _admin_sorted(us: Sequence[_U]) -> list[_U]:
    return sorted(us, key=lambda u: (u.admin_key, u.node.id_rank))


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructureDecision:
    """The decision over one assessed population (public refs only).

    :param selected_refs: What the outcome selects: every tied minimum, the qualified determinations, or the
        selected protocol's cohort id. Empty when nothing is selected.
    :param administrative_first: A labelled administrative presentation, set only when the request asks for
        ``result_mode=first`` and the outcome leaves several alternatives; it never moves an outcome.
    :param cohorts: Each cohort's own ordering and conditional minimum (numerical intents) or its protocol entry.
    :param unresolved: Disclosures that explain an unresolved or partial answer, each with its public refs.
    """

    outcome: StructureOutcome
    basis: str
    selected_refs: tuple[str, ...] = ()
    administrative_first: dict[str, str] | None = None
    cohorts: tuple[dict[str, Any], ...] = ()
    contested_targets: tuple[dict[str, Any], ...] = ()
    unresolved: tuple[dict[str, Any], ...] = ()
    coverage: dict[str, Any] = field(default_factory=dict)
    representative_policy: dict[str, Any] | None = None
    protocol: dict[str, Any] | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_version": DECISION_VERSION,
            "administrative_key": ADMIN_KEY_SPEC,
            "outcome": self.outcome.value,
            "basis": self.basis,
            "selected_refs": list(self.selected_refs),
            "administrative_first": self.administrative_first,
            "cohorts": [dict(c) for c in self.cohorts],
            "contested_targets": [dict(c) for c in self.contested_targets],
            "unresolved": [dict(u) for u in self.unresolved],
            "coverage": dict(self.coverage),
            "representative_policy": self.representative_policy,
            "protocol": self.protocol,
            "search_completeness": SEARCH_COMPLETENESS,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


def _solely_finding_failed(a: StructureAssessment) -> bool:
    if a.physically_eligible:
        return False
    reasons_ok = all(r.code.startswith("finding_unresolved") for r in a.reasons)
    blocks_ok = all(b.startswith(_FINDING_BLOCK_PREFIXES) for b in a.blocking)
    return reasons_ok and blocks_ok and bool(a.reasons or a.blocking)


def _why_none(request: StructureRequest, us: Sequence[_U]) -> StructureOutcome:
    if request.quantity is not None and all(
        u.assessment.reasons
        and not u.assessment.blocking
        and all(r.code in _ENERGY_UNAVAILABLE_CODES for r in u.assessment.reasons)
        for u in us
    ):
        return StructureOutcome.energy_unavailable
    if any(_solely_finding_failed(u.assessment) for u in us):
        return StructureOutcome.evidence_conflict
    return StructureOutcome.no_applicable_candidate


_BASIS_BY_FAILURE = {
    StructureOutcome.energy_unavailable: BASIS_ENERGY_UNAVAILABLE,
    StructureOutcome.evidence_conflict: BASIS_EVIDENCE_CONFLICT,
    StructureOutcome.no_applicable_candidate: BASIS_NONE_APPLICABLE,
}


def _refutes(a: StructureAssessment) -> bool:
    """A known disproof of the claim, as opposed to a missing fact."""
    if a.claim is not None and not a.claim.supported and (a.blocking or a.applicability.value == "incompatible"):
        return True
    return any(b.startswith(_FINDING_BLOCK_PREFIXES) for b in a.blocking)


def _qualifies(request: StructureRequest, u: _U) -> bool:
    a = u.assessment
    if not a.physically_eligible:
        return False
    if request.quantity is not None and a.energy is None:
        return False
    claim = request.effective_claim
    return claim is None or (a.claim is not None and a.claim.supported)


# ---------------------------------------------------------------------------
# Numerical cohorts
# ---------------------------------------------------------------------------


def _cohort_entry(
    cid: str, members: Sequence[_U], request: StructureRequest
) -> tuple[dict[str, Any], list[_U], dict[str, Any] | None]:
    """One cohort's ordering. Returns ``(entry, minimum units, unresolved disclosure or None)``."""
    members = _admin_sorted(members)
    by_target: dict[str, list[_U]] = defaultdict(list)
    for u in members:
        by_target[u.target].append(u)
    representative = request.repeat_policy is RepeatPolicy.administrative_representative
    entries: list[dict[str, Any]] = []
    disagreeing: list[dict[str, Any]] = []
    for target, repeats in by_target.items():
        values = sorted({u.assessment.energy.hartree for u in repeats if u.assessment.energy is not None})
        if len(values) > 1 and not representative:
            disagreeing.append(
                {"target": target, "unit_refs": [u.ref for u in repeats], "hartree": values}
            )
            continue
        rep = repeats[0]
        assert rep.assessment.energy is not None
        entries.append(
            {
                "target": target,
                "representative_ref": rep.ref,
                "unit_refs": [u.ref for u in repeats],
                "hartree": rep.assessment.energy.hartree,
                "scope": rep.assessment.energy.scope.value,
                "uncertainty_hartree": rep.assessment.energy.uncertainty_hartree,
                "alternate_hartree": [v for v in values if v != rep.assessment.energy.hartree] if len(values) > 1 else [],
                "_key": (rep.assessment.energy.hartree, rep.admin_key, rep.node.id_rank),
            }
        )
    entries.sort(key=lambda e: e["_key"])
    rank = 0
    previous: float | None = None
    ordering: list[dict[str, Any]] = []
    for e in entries:
        if previous is None or e["hartree"] != previous:
            rank += 1
            previous = e["hartree"]
        ordering.append({"rank": rank, **{k: v for k, v in e.items() if k != "_key"}})
    first_member = members[0]
    recipe = first_member.assessment.recipe
    # Under the representative policy a target's value is its representative's, which may not be the lowest value stored
    # for it. When that substitution actually happened the cohort's figure is labelled as the representative minimum and
    # never as "the minimum": the true all-values minimum is not claimed anywhere in the result.
    substituted = representative and any(o["alternate_hartree"] for o in ordering)
    minimum_key = "representative_minimum_hartree" if substituted else "minimum_hartree"
    entry: dict[str, Any] = {
        "cohort_id": cid,
        "recipe_facts": [f.to_dict() for f in recipe.facts] if recipe is not None else [],
        "unit_count": len(members),
        "target_count": len(by_target),
        "ordering": ordering,
        minimum_key: ordering[0]["hartree"] if ordering else None,
        "representative_substituted": substituted,
        "minimum_refs": [],
        "unresolved_repeats": disagreeing,
    }
    minimum_units: list[_U] = []
    if ordering and not disagreeing:
        top = [o for o in ordering if o["rank"] == 1]
        wanted = {r for o in top for r in ([o["representative_ref"]] if representative else o["unit_refs"])}
        minimum_units = [u for u in members if u.ref in wanted]
        entry["minimum_refs"] = [u.ref for u in minimum_units]
    elif disagreeing:
        entry[minimum_key] = None
        entry["ordering"] = []
        entry["note"] = "repeat determinations of one target disagree; this cohort is not ordered"
    disclosure = None
    if disagreeing:
        disclosure = {
            "code": "repeat_determinations_disagree",
            "cohort_id": cid,
            "refs": sorted(r for d in disagreeing for r in d["unit_refs"]),
        }
    return entry, minimum_units, disclosure


def _first(request: StructureRequest, ordered: Sequence[_U], *, what: str) -> dict[str, str] | None:
    if request.result_mode is not ResultMode.first or not ordered:
        return None
    return {"ref": ordered[0].ref, "basis": what}


_ADMIN_TIE_BASIS = "administrative order among tied references; not a claim that this record is method-superior"


def _numerical(request: StructureRequest, eligible: list[_U], everyone: list[_U], contested: list[dict[str, Any]]) -> StructureDecision:
    cohorts: dict[str, list[_U]] = defaultdict(list)
    uncohorted: list[_U] = []
    for u in eligible:
        (cohorts[u.cohort] if u.cohort is not None else uncohorted).append(u)
    unresolved: list[dict[str, Any]] = []
    if uncohorted:
        unresolved.append({"code": "cohort_not_established", "refs": sorted(u.ref for u in uncohorted)})
    complete = request.coverage is CoverageRequirement.all_requested_members
    coverage = {
        "requirement": request.coverage.value,
        "requested_units": len(everyone),
        "eligible_units": len(eligible),
        "cohorts": len(cohorts),
        "authorized_population_only": True,
    }
    entries: list[dict[str, Any]] = []
    minima: dict[str, list[_U]] = {}
    for cid in sorted(cohorts):
        entry, minimum_units, disclosure = _cohort_entry(cid, cohorts[cid], request)
        entries.append(entry)
        if disclosure is not None:
            unresolved.append(disclosure)
        if minimum_units:
            minima[cid] = minimum_units
    representative = (
        {"policy": request.repeat_policy.value, "basis": BASIS_REPRESENTATIVE, "administrative_key": ADMIN_KEY_SPEC}
        if request.repeat_policy is RepeatPolicy.administrative_representative
        else None
    )
    common: dict[str, Any] = {
        "cohorts": tuple(entries),
        "contested_targets": tuple(contested),
        "unresolved": tuple(unresolved),
        "representative_policy": representative,
    }
    if complete and (len(cohorts) != 1 or uncohorted or len(minima) != len(cohorts)):
        # A complete claim needs every requested member ordered in one cohort. (A contested target removes its units
        # from the eligible set before this point, so the complete-claim shortfall is reported by the caller.)
        why = "multiple_cohorts" if len(cohorts) > 1 else "cohort_not_established"
        unresolved_all = [*unresolved, {"code": f"complete_claim_not_established:{why}", "refs": sorted(u.ref for u in eligible)}]
        return StructureDecision(
            StructureOutcome.unresolved_comparability,
            BASIS_UNRESOLVED,
            coverage={**coverage, "complete": False},
            **{**common, "unresolved": tuple(unresolved_all)},
        )
    if not minima:
        return StructureDecision(
            StructureOutcome.unresolved_comparability, BASIS_UNRESOLVED, coverage={**coverage, "complete": False}, **common
        )
    cov = {**coverage, "complete": complete}
    if len(cohorts) > 1:
        # More than one established cohort: never a single-cohort minimum, whether or not every cohort could be ordered.
        reps = _admin_sorted([_admin_sorted(v)[0] for v in minima.values()])
        unordered = sorted(set(cohorts) - set(minima))
        notes_cross = ["no overall winner by absolute total energy across cohorts"]
        if unordered:
            notes_cross.append(f"{len(unordered)} cohort(s) could not be ordered (see unresolved); they are not dropped from the claim")
        return StructureDecision(
            StructureOutcome.incomparable_alternatives,
            BASIS_CROSS_COHORT_PARTIAL if unordered else BASIS_CROSS_COHORT,
            selected_refs=(),
            administrative_first=_first(
                request, reps, what="administrative order among cohort minima; total energies of different protocols are not comparable"
            ),
            coverage=cov,
            notes=tuple(notes_cross),
            **common,
        )
    (cid,) = minima
    chosen = _admin_sorted(minima[cid])
    validated = request.intent in (Intent.validated_minimum, Intent.validated_saddle)
    outcome = StructureOutcome.validated_corpus_minimum if validated else StructureOutcome.recorded_minimum
    basis = BASIS_VALIDATED_MINIMUM if validated else BASIS_RECORDED_MINIMUM
    only = next(e for e in entries if e["cohort_id"] == cid)
    if only["representative_substituted"]:
        # The figure is a representative's, not the lowest stored value: a different outcome, so it cannot be read as one.
        outcome = StructureOutcome.representative_minimum
        basis = BASIS_REPRESENTATIVE_MINIMUM
    notes: list[str] = []
    if len(chosen) > 1:
        notes.append("exact numerical tie: every tied reference is retained")
    if uncohorted:
        notes.append(
            f"{len(uncohorted)} eligible unit(s) have no established cohort (their recipe is not established) and are not "
            "compared; this result is conditional on the one established cohort alone"
        )
    return StructureDecision(
        outcome,
        basis,
        selected_refs=tuple(u.ref for u in chosen),
        administrative_first=_first(request, chosen, what=_ADMIN_TIE_BASIS) if len(chosen) > 1 else None,
        coverage=cov,
        notes=tuple(notes),
        **common,
    )


# ---------------------------------------------------------------------------
# Protocol preference
# ---------------------------------------------------------------------------


def _protocol(
    request: StructureRequest,
    subject: StructureSubject,
    eligible: list[_U],
    everyone: list[_U],
    contested: list[dict[str, Any]],
    rules: Sequence[StructureRule],
) -> StructureDecision:
    validate_rules(rules)
    needed_targets = sorted({u.target for u in everyone})
    cohorts: dict[str, list[_U]] = defaultdict(list)
    uncohorted = [u for u in eligible if u.cohort is None]
    for u in eligible:
        if u.cohort is not None:
            cohorts[u.cohort].append(u)
    unresolved: list[dict[str, Any]] = []
    if uncohorted:
        unresolved.append({"code": "cohort_not_established", "refs": sorted(u.ref for u in uncohorted)})
    candidates: dict[str, ProtocolCandidate] = {}
    uncovered: list[dict[str, Any]] = []
    for cid in sorted(cohorts):
        us = _admin_sorted(cohorts[cid])
        covered = sorted({u.target for u in us})
        recipe = us[0].assessment.recipe
        assert recipe is not None
        if request.grain is Grain.calculation or covered == needed_targets:
            candidates[cid] = ProtocolCandidate(cid, recipe, tuple(u.ref for u in us), tuple(covered))
        else:
            uncovered.append({"cohort_id": cid, "covers": len(covered), "requested_targets": len(needed_targets)})
    coverage = {
        "requirement": request.coverage.value,
        "requested_units": len(everyone),
        "requested_targets": len(needed_targets),
        "eligible_units": len(eligible),
        "complete_protocols": len(candidates),
        "authorized_population_only": True,
    }
    if uncovered:
        unresolved.append({"code": "incomplete_protocol_coverage", "refs": [u["cohort_id"] for u in uncovered]})
    if not candidates:
        return StructureDecision(
            StructureOutcome.unresolved_comparability,
            BASIS_UNRESOLVED,
            contested_targets=tuple(contested),
            unresolved=tuple(unresolved),
            coverage={**coverage, "complete": False},
            protocol={"candidates": [], "uncovered": uncovered},
        )
    if request.coverage is CoverageRequirement.all_requested_members and (uncovered or uncohorted or contested or len(eligible) != len(everyone)):
        return StructureDecision(
            StructureOutcome.unresolved_comparability,
            BASIS_UNRESOLVED,
            contested_targets=tuple(contested),
            unresolved=tuple(unresolved),
            coverage={**coverage, "complete": False},
            protocol={"candidates": [c.to_dict() for c in candidates.values()], "uncovered": uncovered},
        )

    nodes: dict[str, AdminNode] = {}
    for cid in candidates:
        best = _admin_sorted(cohorts[cid])[0]
        nodes[cid] = AdminNode(
            ref=cid,
            id_rank=best.node.id_rank,
            review_status=best.node.review_status,
            created_at=best.node.created_at,
            admin_key=best.admin_key,
        )
    applied = tuple(rules) if request.apply_rules else ()
    edges: list[Edge] = []
    rule_matches: list[dict[str, Any]] = []
    pair_checks: list[dict[str, Any]] = []
    for rule in applied:
        header = {"rule_id": rule.rule_id, "rule_version": rule.version}
        if rule.status != RULE_ACTIVE:
            rule_matches.append({**header, "applied": False, "why": f"rule_status_{rule.status}", "reasons": list(rule.inactive_reasons)})
            continue
        if rule.observable != "structure_energy":
            rule_matches.append({**header, "applied": False, "why": f"rule_observable_{rule.observable}"})
            continue
        if rule.objective is not request.objective or rule.reference_model != request.reference_model:
            rule_matches.append({**header, "applied": False, "why": "objective_or_reference_model_differs"})
            continue
        scope = rule.scope(subject, request)
        if scope.state is not Tri.true:
            why = "scope_unknown" if scope.state is Tri.unknown else "outside_rule_scope"
            rule_matches.append({**header, "applied": False, "why": why, "scope": scope.to_dict()})
            continue
        sides = {cid: (rule.preferred_side(c), rule.yielding_side(c)) for cid, c in candidates.items()}
        for a in candidates.values():
            for b in candidates.values():
                if a.cohort_id == b.cohort_id:
                    continue
                if sides[a.cohort_id][0].state is not Tri.true or sides[b.cohort_id][1].state is not Tri.true:
                    continue
                compatible = rule.compatible(a, b)
                pair_checks.append(
                    {**header, "preferred": a.cohort_id, "dispreferred": b.cohort_id, "compatible": compatible.to_dict()}
                )
                if compatible.state is Tri.true:
                    edges.append(Edge(a.cohort_id, b.cohort_id, rule.rule_id, rule.version))
        rule_matches.append(
            {
                **header,
                "applied": True,
                "scope": scope.to_dict(),
                "protocols": [
                    {"cohort_id": cid, "preferred": sides[cid][0].to_dict(), "yielding": sides[cid][1].to_dict()}
                    for cid in sorted(sides)
                ],
            }
        )
    supersedes = {r.rule_id: r.supersedes for r in applied}
    verdict = decide_graph(list(nodes.values()), edges, supersedes=supersedes, admin_policy=SelectionPolicy.default)
    graph_outcome = StructureOutcome(verdict.outcome.value)
    first = None
    if (
        verdict.outcome is Outcome.incomparable_alternatives
        and verdict.administrative_first_ref is not None
        and request.result_mode is ResultMode.first
    ):
        first = {"ref": verdict.administrative_first_ref, "basis": ADMIN_FIRST_BASIS}
    entries = [
        {
            "cohort_id": cid,
            "unit_refs": list(c.unit_refs),
            "target_count": len(c.target_keys),
            "recipe_facts": [f.to_dict() for f in c.recipe.facts],
        }
        for cid, c in sorted(candidates.items())
    ]
    protocol = {
        "objective": request.objective.value if request.objective is not None else None,
        "reference_model": request.reference_model,
        "rules_applied": request.apply_rules,
        "rules": [r.describe() for r in applied],
        "rule_matches": rule_matches,
        "pair_checks": pair_checks,
        "edges": [{**e.to_dict(), "label": EDGE_LABEL} for e in verdict.edges],
        "overridden_edges": [dict(e) for e in verdict.overridden_edges],
        "opposing_pairs": [dict(p) for p in verdict.opposing_pairs],
        "cycles": [list(c) for c in verdict.cycles],
        "fronts": [list(f) for f in verdict.fronts],
        "administrative_order": list(verdict.administrative_order),
        "uncovered": uncovered,
    }
    return StructureDecision(
        graph_outcome,
        verdict.basis,
        selected_refs=(verdict.selected_ref,) if verdict.selected_ref is not None else (),
        administrative_first=first,
        cohorts=tuple(entries),
        contested_targets=tuple(contested),
        unresolved=tuple(unresolved),
        coverage={**coverage, "complete": not uncovered},
        protocol=protocol,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def decide_structures(
    *,
    request: StructureRequest,
    subject: StructureSubject,
    units: Sequence[NormalizedCalculation | NormalizedDetermination],
    assessments: Sequence[StructureAssessment],
    calculations: dict[str, NormalizedCalculation],
    rules: Sequence[StructureRule],
) -> StructureDecision:
    """Decide over every assessed unit. Pure; see the module docstring for the semantics.

    :param units: The units, in the order the loader returned them (their ordinal is ``id_rank``).
    :param assessments: One assessment per unit. Replay passes freshly *recomputed* ones, never recorded ones.
    :param calculations: Every loaded calculation by public ref (the sources a determination's assessment used).
    """
    if not assessments:
        return StructureDecision(StructureOutcome.no_candidates, BASIS_NO_CANDIDATES, coverage={"requested_units": 0})
    us = build_units(units, assessments, calculations, request.admin_policy)

    # A target with an eligible determination and a refuted one is contested: the pass is not an answer.
    claim_needed = request.effective_claim is not None and request.grain is not Grain.calculation
    contested: list[dict[str, Any]] = []
    contested_targets: set[str] = set()
    if claim_needed:
        by_target: dict[str, list[_U]] = defaultdict(list)
        for u in us:
            by_target[u.target].append(u)
        for key in sorted(by_target):
            members = by_target[key]
            passing = [u for u in members if _qualifies(request, u)]
            refuting = [u for u in members if _refutes(u.assessment)]
            if passing and refuting:
                contested_targets.add(key)
                contested.append(
                    {
                        "target": key,
                        "qualifying_refs": sorted(u.ref for u in passing),
                        "refuting_refs": sorted(u.ref for u in refuting),
                    }
                )
    eligible = [u for u in us if _qualifies(request, u) and u.target not in contested_targets]

    if not eligible:
        if contested:
            return StructureDecision(
                StructureOutcome.evidence_conflict,
                BASIS_EVIDENCE_CONFLICT,
                contested_targets=tuple(contested),
                coverage={"requested_units": len(us), "eligible_units": 0},
            )
        outcome = _why_none(request, us)
        return StructureDecision(
            outcome, _BASIS_BY_FAILURE[outcome], coverage={"requested_units": len(us), "eligible_units": 0}
        )

    if request.intent is Intent.qualify_evidence:
        ordered = _admin_sorted(eligible)
        complete = request.coverage is CoverageRequirement.all_requested_members
        if complete and len(eligible) != len(us):
            missing = sorted(u.ref for u in us if u not in eligible)
            return StructureDecision(
                StructureOutcome.evidence_conflict if contested else StructureOutcome.unresolved_comparability,
                BASIS_EVIDENCE_CONFLICT if contested else BASIS_UNRESOLVED,
                contested_targets=tuple(contested),
                unresolved=({"code": "requested_member_not_qualified", "refs": missing},),
                coverage={
                    "requirement": request.coverage.value,
                    "requested_units": len(us),
                    "eligible_units": len(eligible),
                    "complete": False,
                    "authorized_population_only": True,
                },
            )
        return StructureDecision(
            StructureOutcome.qualified_evidence,
            BASIS_QUALIFIED,
            selected_refs=tuple(u.ref for u in ordered),
            administrative_first=_first(request, ordered, what=_ADMIN_TIE_BASIS) if len(ordered) > 1 else None,
            contested_targets=tuple(contested),
            coverage={
                "requirement": request.coverage.value,
                "requested_units": len(us),
                "eligible_units": len(eligible),
                "complete": complete and not contested,
                "authorized_population_only": True,
            },
        )

    if request.coverage is CoverageRequirement.all_requested_members and len(eligible) != len(us):
        if request.intent is not Intent.protocol_preferred:
            missing = sorted(u.ref for u in us if u not in eligible)
            return StructureDecision(
                StructureOutcome.evidence_conflict if contested else StructureOutcome.unresolved_comparability,
                BASIS_EVIDENCE_CONFLICT if contested else BASIS_UNRESOLVED,
                contested_targets=tuple(contested),
                unresolved=({"code": "requested_member_not_eligible", "refs": missing},),
                coverage={
                    "requirement": request.coverage.value,
                    "requested_units": len(us),
                    "eligible_units": len(eligible),
                    "complete": False,
                    "authorized_population_only": True,
                },
            )

    if request.intent is Intent.protocol_preferred:
        return _protocol(request, subject, eligible, us, contested, rules)
    return _numerical(request, eligible, us, contested)


__all__ = [
    "ADMIN_KEY_SPEC",
    "ADMIN_KEY_VERSION",
    "DECISION_VERSION",
    "StructureDecision",
    "admin_key",
    "build_units",
    "cohort_id",
    "decide_structures",
    "target_key",
    "unit_ref",
]
