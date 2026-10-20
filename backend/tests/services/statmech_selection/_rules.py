"""Synthetic rules and populations for the decision tests. Nothing here is a shipped rule: this release ships none."""

from __future__ import annotations

from datetime import timedelta

from app.db.models.common import RecordReviewStatus
from app.services.selection_kernel import RuleMatch, Tri
from app.services.statmech_selection.assessment import StatmechAssessment
from app.services.statmech_selection.decision import StatmechDecision, decide_statmech
from app.services.statmech_selection.facts import CandidateFacts
from app.services.statmech_selection.rules import RULE_ACTIVE, RULE_INACTIVE, ModelCandidate, StatmechRule
from app.services.statmech_selection.vocabulary import Objective
from tests.services.statmech_selection._support import T0, assess, declared, req, subject

DIGEST = "ab" * 32
EXPECTED = Objective.expected_accuracy


def ref(letter: str) -> str:
    return "smd_" + letter * 26


class SyntheticRule(StatmechRule):
    """A rule over named refs: ``prefer`` and ``yield_`` map a ref to a Tri; a ref not named is refuted (``false``)."""

    def __init__(
        self,
        rule_id: str,
        *,
        prefer: dict[str, Tri] | None = None,
        yield_: dict[str, Tri] | None = None,
        objective: Objective | None = EXPECTED,
        reference_model: str | None = None,
        status: str = RULE_ACTIVE,
        supersedes: tuple[str, ...] = (),
        scope: Tri = Tri.true,
        coverage: Tri = Tri.true,
        tasks: tuple[str, ...] = ("within_basin_thermal",),
        observables: tuple[str, ...] = ("entropy",),
        compat: Tri = Tri.true,
        digest: str | None = DIGEST,
    ) -> None:
        # A rule id is a registry name (at least two characters, no version in it); a one-letter test id is doubled.
        self.rule_id = rule_id if len(rule_id) >= 2 else rule_id * 2
        self.version = 1
        self._prefer, self._yield = prefer or {}, yield_ or {}
        self.objective = objective
        self.reference_model = reference_model
        self._status = status
        self.supersedes = supersedes
        self._scope, self._coverage, self._compat = scope, coverage, compat
        self.tasks, self.observables = tasks, observables
        self.manifest_sha256 = digest
        self.inactive_reasons = ("synthetic: inactive",) if status == RULE_INACTIVE else ()

    @property
    def status(self) -> str:
        return self._status

    def scope(self, subject, request):
        return RuleMatch(self._scope, ())

    def coverage(self, request):
        return RuleMatch(self._coverage, ())

    def preferred_side(self, candidate: ModelCandidate):
        return RuleMatch(self._prefer.get(candidate.candidate_ref, Tri.false), ())

    def yielding_side(self, candidate: ModelCandidate):
        return RuleMatch(self._yield.get(candidate.candidate_ref, Tri.false), ())

    def compatible(self, a, b):
        return RuleMatch(self._compat, ())


def beats(rule_id: str, winner: str, *losers: str, **kw) -> SyntheticRule:
    """A rule under which ``winner`` precedes every one of ``losers``."""
    return SyntheticRule(rule_id, prefer={ref(winner): Tri.true}, yield_={ref(x): Tri.true for x in losers}, **kw)


def candidate(letter: str, *, rank: int, review=RecordReviewStatus.approved, age_days: int = 0, recipe: dict | None = None, **changes):
    return declared(
        candidate_ref=ref(letter),
        id_rank=rank,
        review_status=review,
        created_at=T0 + timedelta(days=age_days),
        recipe=recipe,
        **changes,
    )


def population(*letters: str, **per_letter):
    """Candidates a..., each qualified for the default thermal request; ``per_letter['a'] = {...}`` overrides."""
    out = []
    for rank, letter in enumerate(letters, start=1):
        out.append(candidate(letter, rank=rank, **per_letter.get(letter, {})))
    return out


def run(candidates: list[CandidateFacts], rules=(), request=None, **request_changes) -> tuple[StatmechDecision, list[StatmechAssessment]]:
    request = request or req(
        {**_PREFERRED, **request_changes.pop("body", {})}, **request_changes
    )
    # the service assesses nothing for a deferred task (it answers unsupported_task without loading candidates)
    assessments = [] if request.deferred else [assess(c, request) for c in candidates]
    return (
        decide_statmech(request=request, subject=subject(), candidates=candidates, assessments=assessments, rules=rules),
        assessments,
    )


from tests.services.statmech_selection.test_request import THERMAL

_PREFERRED = {**THERMAL, "intent": "protocol_preferred", "comparison_objective": "expected_accuracy"}
