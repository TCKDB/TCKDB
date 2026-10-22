"""The statmech decision: qualification first, then the qualified models in display order.

Pure. It reads no database, so a recorded decision replays from its manifest alone. Input is the request, every
candidate's facts and its assessment; output is a :class:`StatmechDecision` over public refs.

What is specific to statmech and lives here, and not in the shared kernel:

* **Qualification is the floor.** ``intent=qualify`` returns the qualification summary and no selection reference.
  ``protocol_coverage`` goes on to list the qualified models only when the population was decided: if the summary is
  anything but ``qualified_models`` (an unsupported request, no candidates, nothing qualifying, or qualified models
  alongside undecided alternatives under a request for the entire domain), that outcome is the answer and every
  selection reference is null. The qualified subset stays visible. Required full coverage is never certified by a
  subset.
* **Nothing ranks one model above another.** TCKDB carries no ranking between methods or whole models: one qualified
  model is a sole eligible candidate, several are incomparable alternatives listed in administrative order
  (``docs/specs/browse_order.md``). A model is never assembled from favourable pieces.
* **The ordering is the shared kernel's.** The qualified models go to :func:`decide_alternatives`, which yields the
  outcome and the administrative order.
* **Two mutually exclusive references.** ``sole_eligible_ref`` for a lone eligible model (noncomparative),
  ``administrative_ref`` only for ``result_mode=first`` among several eligible alternatives (first in display order);
  each carries its basis and its population limit. An administrative reference never rescues an unresolved outcome or
  an incomplete population.
* **Advisories travel.** Per-candidate advisories (an imported source whose own stationary-point statement is not
  re-judged, a literature citation with no DOI or ISBN) are part of the decision, not only of the assessment.

The administrative key (versioned, echoed with every decision): own review rank ascending, creation time descending,
captured public ordinal descending; ``latest`` is time and ordinal descending only. It never substitutes the
parent's review or a member's energy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.schemas.reads.scientific_common import REVIEW_RANK, SelectionPolicy
from app.services.selection_kernel import ADMIN_FIRST_BASIS, AdminNode, decide_alternatives
from app.services.selection_kernel import Outcome as GraphOutcome
from app.services.statmech_selection.assessment import (
    QualificationSummary,
    StatmechAssessment,
    summarize,
)
from app.services.statmech_selection.facts import CandidateFacts
from app.services.statmech_selection.models import ADMIN_KEY_VERSION, DECISION_VERSION, StatmechRequest
from app.services.statmech_selection.vocabulary import Intent, Outcome, ResultMode

ADMIN_KEY_SPEC: dict[str, Any] = {
    "version": ADMIN_KEY_VERSION,
    "default": ["review_rank ascending", "created_at descending", "ordinal descending"],
    "latest": ["created_at descending", "ordinal descending"],
}

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

BASIS_SOLE = "the only eligible model in the authorized population; not a comparative or global-best claim"
BASIS_INCOMPARABLE = (
    "more than one model is eligible; TCKDB carries no ranking between methods, so none is preferred and they are "
    "listed in display order only"
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
    """The two mutually exclusive references of a decision, with the basis and the population limit of the one set."""

    sole_eligible_ref: str | None = None
    administrative_ref: str | None = None
    basis: str | None = None
    limitation: str | None = None

    def __post_init__(self) -> None:
        refs = [r for r in (self.sole_eligible_ref, self.administrative_ref) if r is not None]
        if len(refs) > 1:
            raise ValueError("sole_eligible_ref and administrative_ref are mutually exclusive")
        if refs and (self.basis is None or self.limitation is None):
            raise ValueError("a selection carries its basis and its population limitation")
        if not refs and (self.basis is not None or self.limitation is not None):
            raise ValueError("a basis and a limitation belong to a selection reference")

    def to_dict(self) -> dict[str, Any]:
        return {
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
    ordering: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "basis": self.basis,
            "selection": self.selection.to_dict(),
            "summary": self.summary.to_dict() if self.summary is not None else None,
            "coverage": self.coverage,
            "advisories": {k: list(v) for k, v in sorted(self.advisories.items())},
            "notices": list(self.notices),
            "ordering": self.ordering,
            "versions": {"decision": DECISION_VERSION, "administrative_key": ADMIN_KEY_VERSION},
        }


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


def _ordering(fronts: Sequence[Sequence[str]] = (), order: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "fronts": [list(f) for f in fronts],
        "administrative_order": list(order),
        "admin_key": ADMIN_KEY_SPEC,
    }


def decide_statmech(
    *,
    request: StatmechRequest,
    candidates: Sequence[CandidateFacts],
    assessments: Sequence[StatmechAssessment],
) -> StatmechDecision:
    """Decide over every assessed candidate. Pure; see the module docstring for the semantics.

    :param candidates: The candidates, in the order the loader returned them.
    :param assessments: One assessment per candidate. Replay passes freshly *recomputed* ones, never recorded ones.
    """
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
            ordering=_ordering(),
        )
    if base_outcome is not Outcome.qualified_models:
        # Not decided enough to list: the unresolved outcome is the answer; the qualified subset stays visible.
        return StatmechDecision(
            base_outcome,
            summary.basis,
            summary=summary,
            coverage=_coverage(request, summary, certified=False),
            advisories=advisories,
            notices=tuple(notices),
            ordering=_ordering(),
        )

    # --- the qualified models, in administrative order -----------------------------------------------------------
    nodes = [
        AdminNode(
            ref=a.candidate_ref,
            id_rank=by_ref[a.candidate_ref].id_rank,
            review_status=by_ref[a.candidate_ref].review_status,
            created_at=by_ref[a.candidate_ref].created_at,
            admin_key=admin_key(request.admin_policy, by_ref[a.candidate_ref]),
        )
        for a in sorted(qualified, key=lambda a: a.candidate_ref)
    ]
    verdict = decide_alternatives(nodes, admin_policy=request.admin_policy)
    partial = bool(summary.undecided_refs)  # only under eligible_population coverage: otherwise the summary refused above
    limitation = LIMIT_ELIGIBLE_ONLY if partial else LIMIT_AUTHORIZED

    def result(outcome: Outcome, basis: str, selection: Selection = Selection()) -> StatmechDecision:
        return StatmechDecision(
            outcome,
            basis,
            selection=selection,
            summary=summary,
            coverage=_coverage(request, summary, certified=not partial),
            advisories=advisories,
            notices=tuple(notices),
            ordering=_ordering(verdict.fronts, verdict.administrative_order),
        )

    if verdict.outcome is GraphOutcome.sole_eligible_candidate:
        assert verdict.selected_ref is not None
        return result(
            Outcome.sole_eligible_candidate,
            BASIS_SOLE,
            Selection(sole_eligible_ref=verdict.selected_ref, basis=BASIS_SOLE, limitation=limitation),
        )
    selection = Selection()
    if request.result_mode is ResultMode.first and verdict.administrative_first_ref is not None:
        selection = Selection(administrative_ref=verdict.administrative_first_ref, basis=ADMIN_FIRST_BASIS, limitation=limitation)
    return result(Outcome.incomparable_alternatives, BASIS_INCOMPARABLE, selection)


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
