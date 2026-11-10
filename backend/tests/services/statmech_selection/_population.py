"""Populations for the statmech decision tests. This release ranks no model above another, so there are no rules here."""

from __future__ import annotations

from datetime import timedelta

from app.db.models.common import RecordReviewStatus
from app.services.statmech_selection.assessment import StatmechAssessment
from app.services.statmech_selection.decision import StatmechDecision, decide_statmech
from app.services.statmech_selection.facts import CandidateFacts
from tests.services.statmech_selection._support import T0, assess, declared, req
from tests.services.statmech_selection.test_request import THERMAL


def ref(letter: str) -> str:
    return "smd_" + letter * 26


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


_PREFERRED = {**THERMAL, "intent": "protocol_coverage"}


def run(candidates: list[CandidateFacts], request=None, **request_changes) -> tuple[StatmechDecision, list[StatmechAssessment]]:
    request = request or req({**_PREFERRED, **request_changes.pop("body", {})}, **request_changes)
    # the service assesses nothing for a deferred task (it answers unsupported_task without loading candidates)
    assessments = [] if request.deferred else [assess(c, request) for c in candidates]
    return decide_statmech(request=request, candidates=candidates, assessments=assessments), assessments
