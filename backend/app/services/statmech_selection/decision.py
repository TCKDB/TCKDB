"""The statmech decision: qualification first, then, only where audited rules say so, a whole-model preference.

Pure. It reads no database, so a recorded decision replays from its manifest alone. Input is the request, every
candidate's facts, its assessment and the registry; output is a :class:`StatmechDecision` over public refs.

What is specific to statmech and lives here, and not in the shared kernel:

* **Qualification is the floor.** ``intent=qualify`` returns the qualification summary and no selection reference.
  ``protocol_preferred`` compares only when the population was decided: if the summary is anything but
  ``qualified_models`` (an unsupported request, no candidates, nothing qualifying, or qualified models alongside
  undecided alternatives under a request for the entire domain), that outcome is the answer and every selection
  reference is null. The qualified subset stays visible. Required full coverage is never certified by a subset.
* **Whole models only.** A rule compares two complete models, each already ``applicable`` for this request. A model is
  never assembled from favourable pieces, a per-member or per-temperature win creates no mixed-model winner, and a
  rule whose audited coverage does not contain the entire requested scope applies to nothing.
* **Unknown is reported, not dropped.** A pair for which a rule *might* give an edge, but a prerequisite is unknown on
  one side, is listed as undecided and the outcome is ``unresolved_comparability``: a preference claim over a graph
  that an unestablished edge could change would be false.
* **The graph is the shared kernel's.** Edges built by active rules go to :func:`decide_graph`: supersession of
  rules is settled before any opposition is judged, a surviving opposing pair or a cycle is a ``policy_conflict``
  with no selection, and fronts and the administrative order come out. Every edge in one decision answers one
  question: a rule applies only under the request's objective (and reference model).
* **Three mutually exclusive references.** ``scientific_ref`` only for supported dominance over every eligible
  alternative, ``sole_eligible_ref`` for a lone eligible model (noncomparative), ``administrative_ref`` only for
  ``result_mode=first`` among incomparable first-front alternatives; each carries its basis and its population limit.
  An administrative reference never rescues a conflict, an unresolved comparability or an incomplete population.
* **Advisories travel.** Per-candidate advisories (an imported source whose own stationary-point statement is not
  re-judged, a literature citation with no DOI or ISBN) are part of the decision, not only of the assessment.

The administrative key (versioned, echoed with every decision): own review rank ascending, creation time descending,
captured public ordinal descending; ``latest`` is time and ordinal descending only. It sorts within a front and
nothing else; it never substitutes the parent's review or a member's energy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.schemas.reads.scientific_common import REVIEW_RANK, SelectionPolicy
from app.services.selection_kernel import ADMIN_FIRST_BASIS, AdminNode, Edge, Tri, decide_graph
from app.services.selection_kernel import Outcome as GraphOutcome
from app.services.statmech_selection.assessment import (
    QualificationSummary,
    StatmechAssessment,
    summarize,
)
from app.services.statmech_selection.facts import CandidateFacts, SubjectFacts
from app.services.statmech_selection.models import ADMIN_KEY_VERSION, DECISION_VERSION, StatmechRequest
from app.services.statmech_selection.rules import (
    EDGE_LABEL,
    RULE_ACTIVE,
    RULES_VERSION,
    ModelCandidate,
    StatmechRule,
    validate_rules,
)
from app.services.statmech_selection.vocabulary import Intent, Outcome, ResultMode

ADMIN_KEY_SPEC: dict[str, Any] = {
    "version": ADMIN_KEY_VERSION,
    "default": ["review_rank ascending", "created_at descending", "ordinal descending"],
    "latest": ["created_at descending", "ordinal descending"],
}

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

BASIS_SOLE = "the only eligible model in the authorized population; not a comparative or global-best claim"
BASIS_PREFERRED = (
    "the only model with no incoming preference edge: every other eligible model is reached from it by accepted "
    "audited rule preferences under the stated objective"
)
BASIS_INCOMPARABLE = "more than one model has no incoming preference edge; the audited rules do not rank them"
BASIS_INCOMPARABLE_NO_RULE = "no active rule compares the applicable recipes; this does not establish equal accuracy"
BASIS_CONFLICT = "opposing rules or a preference cycle make the model order self-contradictory; no automatic selection"
BASIS_UNRESOLVED_COMPARABILITY = (
    "an audited rule might compare these models but a prerequisite is not established for one of them; the order is "
    "not established and nothing is selected"
)
BASIS_QUALIFY = "qualification only: the request asks which models support the claim, not which is preferred"
LIMIT_AUTHORIZED = (
    "coverage is the caller's authorized population only; it proves neither a corpus-wide best nor an exhaustive "
    "physical search"
)
LIMIT_ELIGIBLE_ONLY = (
    "the answer is scoped to the eligible models: other candidates are unresolved or unsupported and might compete if "
    "their missing facts were stated or their form evaluated"
)

#: Advisory prefixes the decision surfaces to its reader, and the sentence each one earns as a decision notice.
SURFACED_ADVISORIES: dict[str, str] = {
    "imported_parameters_not_revalidated": (
        "imported literature parameters stand for a role without a local job; the stationary-point character they were "
        "computed at is the source's own statement and was not re-judged"
    ),
    "source_literature_unidentified": (
        "a cited literature source has no DOI or ISBN, so it is kept on the record but meets no role"
    ),
}


def _micros(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment - _EPOCH) // timedelta(microseconds=1)


def admin_key(policy: SelectionPolicy, candidate: CandidateFacts) -> tuple:
    """The versioned administrative key (see :data:`ADMIN_KEY_SPEC`); direction is already baked in."""
    micros = _micros(candidate.created_at)
    if policy is SelectionPolicy.latest:
        return (-micros, -candidate.id_rank)
    return (REVIEW_RANK[candidate.review_status], -micros, -candidate.id_rank)


@dataclass(frozen=True)
class Selection:
    """The three mutually exclusive references of a decision, with the basis and the population limit of the one set."""

    scientific_ref: str | None = None
    sole_eligible_ref: str | None = None
    administrative_ref: str | None = None
    basis: str | None = None
    limitation: str | None = None

    def __post_init__(self) -> None:
        refs = [r for r in (self.scientific_ref, self.sole_eligible_ref, self.administrative_ref) if r is not None]
        if len(refs) > 1:
            raise ValueError("scientific_ref, sole_eligible_ref and administrative_ref are mutually exclusive")
        if refs and (self.basis is None or self.limitation is None):
            raise ValueError("a selection carries its basis and its population limitation")
        if not refs and (self.basis is not None or self.limitation is not None):
            raise ValueError("a basis and a limitation belong to a selection reference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scientific_ref": self.scientific_ref,
            "sole_eligible_ref": self.sole_eligible_ref,
            "administrative_ref": self.administrative_ref,
            "basis": self.basis,
            "limitation": self.limitation,
        }


@dataclass(frozen=True)
class StatmechDecision:
    """The decision over one assessed population, by public refs only."""

    outcome: Outcome
    basis: str
    selection: Selection = field(default_factory=Selection)
    summary: QualificationSummary | None = None
    coverage: dict[str, Any] = field(default_factory=dict)
    advisories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    notices: tuple[str, ...] = ()
    protocol: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "basis": self.basis,
            "selection": self.selection.to_dict(),
            "summary": self.summary.to_dict() if self.summary is not None else None,
            "coverage": self.coverage,
            "advisories": {k: list(v) for k, v in sorted(self.advisories.items())},
            "notices": list(self.notices),
            "protocol": self.protocol,
            "versions": {"decision": DECISION_VERSION, "administrative_key": ADMIN_KEY_VERSION, "rules": RULES_VERSION},
        }


def _model(candidate: CandidateFacts, assessment: StatmechAssessment) -> ModelCandidate:
    assert assessment.recipe is not None
    return ModelCandidate(
        candidate_ref=candidate.candidate_ref,
        kind=candidate.kind,
        owner_ref=candidate.owner_ref,
        recipe=assessment.recipe,
        evidence=tuple(sorted((k, tuple(v)) for k, v in assessment.evidence.items())),
    )


def _advisories(assessments: Sequence[StatmechAssessment]) -> dict[str, tuple[str, ...]]:
    return {a.candidate_ref: tuple(sorted(set(a.advisory))) for a in assessments if a.advisory}


def _notices(assessments: Sequence[StatmechAssessment]) -> list[str]:
    seen = {adv.split(":")[0] for a in assessments for adv in a.advisory}
    return [text for prefix, text in SURFACED_ADVISORIES.items() if prefix in seen]


def _coverage(request: StatmechRequest, summary: QualificationSummary, *, certified: bool) -> dict[str, Any]:
    return {
        "requirement": request.coverage.value,
        "authorized_population": "complete",
        "physical_search": "not_established",
        "qualified": len(summary.qualified_refs),
        "undecided": len(summary.undecided_refs),
        "certified_for_requested_domain": certified,
    }


def _unranked(rules: Sequence[StatmechRule], request: StatmechRequest) -> dict[str, Any]:
    return {
        "objective": request.objective.value if request.objective is not None else None,
        "reference_model": request.reference_model_ref,
        "rules_applied": False,
        "rules": [r.describe() for r in rules],
        "rule_matches": [],
        "pair_checks": [],
        "undecided": [],
        "edges": [],
        "overridden_edges": [],
        "opposing_pairs": [],
        "cycles": [],
        "fronts": [],
        "administrative_order": [],
        "admin_key": ADMIN_KEY_SPEC,
    }


def decide_statmech(
    *,
    request: StatmechRequest,
    subject: SubjectFacts,
    candidates: Sequence[CandidateFacts],
    assessments: Sequence[StatmechAssessment],
    rules: Sequence[StatmechRule],
) -> StatmechDecision:
    """Decide over every assessed candidate. Pure; see the module docstring for the semantics.

    :param candidates: The candidates, in the order the loader returned them.
    :param assessments: One assessment per candidate. Replay passes freshly *recomputed* ones, never recorded ones.
    :param rules: The registry to apply; validated here, so an active rule that is not pinned cannot make an edge.
    """
    registry = tuple(rules)
    validate_rules(registry)
    by_ref = {c.candidate_ref: c for c in candidates}
    if len(by_ref) != len(candidates):
        raise ValueError("candidate refs must be distinct")
    summary = summarize(request, assessments)
    advisories = _advisories(assessments)
    notices = _notices(assessments)
    qualified = [a for a in assessments if a.eligible]
    base_outcome = Outcome(summary.outcome)

    # --- qualification is the floor ------------------------------------------------------------------------------
    if request.intent is Intent.qualify or request.deferred:
        basis = BASIS_QUALIFY if base_outcome is Outcome.qualified_models else summary.basis
        return StatmechDecision(
            base_outcome,
            basis,
            summary=summary,
            coverage=_coverage(request, summary, certified=base_outcome is Outcome.qualified_models and not summary.undecided_refs),
            advisories=advisories,
            notices=tuple(notices),
            protocol=_unranked(registry, request),
        )
    if base_outcome is not Outcome.qualified_models:
        # Not decided enough to compare: the unresolved outcome is the answer; the qualified subset stays visible.
        return StatmechDecision(
            base_outcome,
            summary.basis,
            summary=summary,
            coverage=_coverage(request, summary, certified=False),
            advisories=advisories,
            notices=tuple(notices),
            protocol=_unranked(registry, request),
        )

    # --- whole-model preference over the qualified models --------------------------------------------------------
    models = {a.candidate_ref: _model(by_ref[a.candidate_ref], a) for a in qualified}
    nodes = [
        AdminNode(
            ref=ref,
            id_rank=by_ref[ref].id_rank,
            review_status=by_ref[ref].review_status,
            created_at=by_ref[ref].created_at,
            admin_key=admin_key(request.admin_policy, by_ref[ref]),
        )
        for ref in sorted(models)
    ]
    edges: list[Edge] = []
    rule_matches: list[dict[str, Any]] = []
    pair_checks: list[dict[str, Any]] = []
    undecided: list[dict[str, Any]] = []
    applied: list[StatmechRule] = []
    for rule in registry:
        header = {"rule_id": rule.rule_id, "rule_version": rule.version}
        if rule.status != RULE_ACTIVE:
            rule_matches.append({**header, "applied": False, "why": f"rule_status_{rule.status}", "reasons": list(rule.inactive_reasons)})
            continue
        if request.task.value not in rule.tasks or (request.observable is not None and request.observable.value not in rule.observables):
            rule_matches.append({**header, "applied": False, "why": "task_or_observable_differs"})
            continue
        if rule.objective is not request.objective or rule.reference_model != request.reference_model_ref:
            rule_matches.append({**header, "applied": False, "why": "objective_or_reference_model_differs"})
            continue
        scope = rule.scope(subject, request)
        if scope.state is not Tri.true:
            why = "scope_unknown" if scope.state is Tri.unknown else "outside_rule_scope"
            rule_matches.append({**header, "applied": False, "why": why, "scope": scope.to_dict()})
            if scope.state is Tri.unknown:
                undecided.append({**header, "code": "comparison_prerequisite_unknown", "what": "scope"})
            continue
        coverage = rule.coverage(request)
        if coverage.state is not Tri.true:
            why = "coverage_unknown" if coverage.state is Tri.unknown else "requested_scope_not_covered"
            rule_matches.append({**header, "applied": False, "why": why, "coverage": coverage.to_dict()})
            if coverage.state is Tri.unknown:
                undecided.append({**header, "code": "comparison_prerequisite_unknown", "what": "coverage"})
            continue
        applied.append(rule)
        sides = {ref: (rule.preferred_side(m), rule.yielding_side(m)) for ref, m in models.items()}
        for a_ref, a in models.items():
            for b_ref, b in models.items():
                if a_ref == b_ref:
                    continue
                pref, yld = sides[a_ref][0], sides[b_ref][1]
                if pref.state is Tri.false or yld.state is Tri.false:
                    continue
                compatible = rule.compatible(a, b)
                if compatible.state is Tri.false:
                    continue
                states = (pref.state, yld.state, compatible.state)
                record = {**header, "preferred": a_ref, "dispreferred": b_ref, "compatible": compatible.to_dict()}
                if all(s is Tri.true for s in states):
                    pair_checks.append({**record, "result": "edge"})
                    edges.append(Edge(a_ref, b_ref, rule.rule_id, rule.version))
                else:
                    pair_checks.append({**record, "result": "undecided"})
                    undecided.append({**header, "code": "comparison_prerequisite_unknown", "preferred": a_ref, "dispreferred": b_ref})
        rule_matches.append(
            {
                **header,
                "applied": True,
                "scope": scope.to_dict(),
                "coverage": coverage.to_dict(),
                "models": [
                    {"candidate_ref": ref, "preferred": sides[ref][0].to_dict(), "yielding": sides[ref][1].to_dict()}
                    for ref in sorted(sides)
                ],
            }
        )
    supersedes = {r.rule_id: r.supersedes for r in registry}
    verdict = decide_graph(nodes, edges, supersedes=supersedes, admin_policy=request.admin_policy)
    protocol = {
        "objective": request.objective.value if request.objective is not None else None,
        "reference_model": request.reference_model_ref,
        "rules_applied": True,
        "rules": [r.describe() for r in registry],
        "rule_matches": rule_matches,
        "pair_checks": pair_checks,
        "undecided": undecided,
        "edges": [{**e.to_dict(), "label": EDGE_LABEL} for e in verdict.edges],
        "overridden_edges": [dict(e) for e in verdict.overridden_edges],
        "opposing_pairs": [dict(p) for p in verdict.opposing_pairs],
        "cycles": [list(c) for c in verdict.cycles],
        "fronts": [list(f) for f in verdict.fronts],
        "administrative_order": list(verdict.administrative_order),
        "admin_key": ADMIN_KEY_SPEC,
    }
    partial = bool(summary.undecided_refs)  # only under eligible_population coverage: otherwise the summary refused above
    limitation = LIMIT_ELIGIBLE_ONLY if partial else LIMIT_AUTHORIZED
    certified = not partial and not undecided

    def result(outcome: Outcome, basis: str, selection: Selection = Selection()) -> StatmechDecision:
        return StatmechDecision(
            outcome,
            basis,
            selection=selection,
            summary=summary,
            coverage=_coverage(request, summary, certified=certified),
            advisories=advisories,
            notices=tuple(notices),
            protocol=protocol,
        )

    if undecided and len(models) > 1:
        return result(Outcome.unresolved_comparability, BASIS_UNRESOLVED_COMPARABILITY)
    if verdict.outcome is GraphOutcome.policy_conflict:
        return result(Outcome.policy_conflict, BASIS_CONFLICT)
    if verdict.outcome is GraphOutcome.sole_eligible_candidate:
        assert verdict.selected_ref is not None
        return result(
            Outcome.sole_eligible_candidate, BASIS_SOLE, Selection(sole_eligible_ref=verdict.selected_ref, basis=BASIS_SOLE, limitation=limitation)
        )
    if verdict.outcome is GraphOutcome.policy_preferred:
        assert verdict.selected_ref is not None
        return result(
            Outcome.policy_preferred, BASIS_PREFERRED, Selection(scientific_ref=verdict.selected_ref, basis=BASIS_PREFERRED, limitation=limitation)
        )
    basis = BASIS_INCOMPARABLE if verdict.edges else BASIS_INCOMPARABLE_NO_RULE
    selection = Selection()
    if request.result_mode is ResultMode.first and verdict.administrative_first_ref is not None:
        selection = Selection(administrative_ref=verdict.administrative_first_ref, basis=ADMIN_FIRST_BASIS, limitation=limitation)
    return result(Outcome.incomparable_alternatives, basis, selection)


__all__ = [
    "ADMIN_KEY_SPEC",
    "ADMIN_KEY_VERSION",
    "DECISION_VERSION",
    "SURFACED_ADVISORIES",
    "Selection",
    "StatmechDecision",
    "admin_key",
    "decide_statmech",
]
