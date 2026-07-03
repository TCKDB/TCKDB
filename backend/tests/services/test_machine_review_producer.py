"""Tests for the provider-shaped machine-review producer interface.

Cover the fake producer (benign defaults, supplied clock) and the
producer-based orchestration path: it appends through the executor for run_*,
skips current without calling the producer, treats producer failure as
``failed_to_produce_review`` (no row), preserves source ids, stays idempotent,
calls no real provider, and mutates nothing outside ``record_machine_review``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db.models.common import SubmissionActorKind, SubmissionAuditEventKind, SubmissionKind
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.submission import SubmissionAuditEvent
from app.services.machine_review import (
    FakeMachineReviewProducer,
    MachineReviewEvidenceContext,
    MachineReviewOrchestrationResult,
    MachineReviewOrchestrationStatus,
    MachineReviewProductionError,
    MachineReviewReReviewDecision,
    MachineReviewStatus,
    RecordMachineReview,
    run_record_machine_review_with_producer,
)
from app.services.submission import create_submission
from app.services.trust.fragment import build_trust_fragment
from app.services.trust.models import EvidenceBadge, EvidenceEvaluation

_T0 = datetime(2026, 6, 1, 12, 0, 0)
_T1 = _T0 + timedelta(hours=1)
_T2 = _T0 + timedelta(hours=2)
_PROMPT = "prompt_v3"
_RUBRICS = {"kinetics": "computed_kinetics_v1"}
_RECORD_ID = 9001
_RECORD_REF = "kin_9001"


def _context(record_type: str = "kinetics", record_ref: str = _RECORD_REF):
    """A minimal evidence context for direct producer tests."""
    return MachineReviewEvidenceContext(record_type=record_type, record_ref=record_ref)


def _fragment(*, missing_checks: tuple[str, ...] = ("uncertainty_present",)):
    evaluation = EvidenceEvaluation(
        record_type="kinetics",
        record_id=_RECORD_ID,
        rubric="computed_kinetics",
        rubric_version=1,
        label=EvidenceBadge.mostly_supported,
        passed_checks=("a_present", "ea_present"),
        missing_checks=missing_checks,
        warning_checks=(),
        not_applicable_checks=(),
        passed_count=2,
        possible_count=3,
        evidence_completeness=0.67,
    )
    return build_trust_fragment(evaluation, review_status="not_reviewed")


def _run(db_session, *, producer=None, fragment=None, reviewed_at=_T0, **kwargs):
    return run_record_machine_review_with_producer(
        db_session,
        record_type="kinetics",
        record_id=_RECORD_ID,
        record_ref=_RECORD_REF,
        trust_fragment=fragment if fragment is not None else _fragment(),
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
        producer=producer if producer is not None else FakeMachineReviewProducer(),
        reviewed_at=reviewed_at,
        **kwargs,
    )


def _count_rows(db_session, record_id=_RECORD_ID) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(RecordMachineReviewRow)
        .where(RecordMachineReviewRow.record_id == record_id)
    )


class _RaisingProducer:
    """A producer that always fails, to exercise the failure path."""

    def __init__(self) -> None:
        self.calls = 0

    def review_record(self, context, *, reviewed_at):
        self.calls += 1
        raise MachineReviewProductionError("boom")


class _CountingProducer:
    """Wraps the fake producer and counts calls (to prove skip avoids it)."""

    def __init__(self) -> None:
        self.calls = 0
        self._inner = FakeMachineReviewProducer()

    def review_record(self, context, *, reviewed_at):
        self.calls += 1
        return self._inner.review_record(context, reviewed_at=reviewed_at)


# --------------------------------------------------------------------------- #
# Fake producer unit behaviour
# --------------------------------------------------------------------------- #


def test_fake_producer_returns_benign_record_review():
    review = FakeMachineReviewProducer().review_record(_context(), reviewed_at=_T0)
    assert isinstance(review, RecordMachineReview)
    assert review.status is MachineReviewStatus.machine_screened_pass
    assert review.findings == ()
    assert review.model == "fake-test"
    assert review.provider == "fake"
    assert review.record_type == "kinetics"
    assert review.record_ref == _RECORD_REF


def test_fake_producer_uses_supplied_reviewed_at():
    review = FakeMachineReviewProducer().review_record(_context(), reviewed_at=_T1)
    assert review.reviewed_at == _T1


def test_fake_producer_can_raise_production_error():
    with pytest.raises(MachineReviewProductionError):
        FakeMachineReviewProducer(raise_error=True).review_record(
            _context(), reviewed_at=_T0
        )


# --------------------------------------------------------------------------- #
# Producer-based orchestration: append / skip
# --------------------------------------------------------------------------- #


def test_producer_orchestration_appends_when_not_reviewed(db_session):
    result = _run(db_session)
    assert result.status is MachineReviewOrchestrationStatus.appended
    assert result.decision is MachineReviewReReviewDecision.run_not_reviewed
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.provider == "fake"
    assert _count_rows(db_session) == 1


def test_producer_orchestration_appends_when_stale(db_session):
    _run(db_session, fragment=_fragment(missing_checks=("uncertainty_present",)), reviewed_at=_T0)
    result = _run(db_session, fragment=_fragment(missing_checks=()), reviewed_at=_T1)
    assert result.status is MachineReviewOrchestrationStatus.appended
    assert result.decision is MachineReviewReReviewDecision.run_stale
    assert _count_rows(db_session) == 2


def test_producer_orchestration_skips_current_without_calling_producer(db_session):
    # Seed a current row.
    _run(db_session, reviewed_at=_T0)
    # Re-run with a counting producer; it must not be called because the plan
    # is skip_current.
    counting = _CountingProducer()
    result = _run(db_session, producer=counting, reviewed_at=_T1)
    assert result.status is MachineReviewOrchestrationStatus.skipped_current
    assert counting.calls == 0
    assert _count_rows(db_session) == 1


def test_producer_orchestration_preserves_source_submission_id(db_session, _api_test_user):
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="producer test",
        summary="src submission",
    )
    db_session.flush()
    result = _run(db_session, source_submission_id=submission.id)
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.source_submission_id == submission.id


def test_producer_orchestration_preserves_source_audit_event_id(db_session, _api_test_user):
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="producer test",
        summary="src event",
    )
    db_session.flush()
    event = SubmissionAuditEvent(
        submission_id=submission.id,
        actor_kind=SubmissionActorKind.llm,
        event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
        details_json={"label": "warning"},
    )
    db_session.add(event)
    db_session.flush()
    result = _run(db_session, source_audit_event_id=event.id)
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.source_audit_event_id == event.id


def test_producer_orchestration_is_idempotent_for_unchanged_recipe(db_session):
    first = _run(db_session, reviewed_at=_T0)
    second = _run(db_session, reviewed_at=_T1)
    assert first.status is MachineReviewOrchestrationStatus.appended
    assert second.status is MachineReviewOrchestrationStatus.skipped_current
    assert _count_rows(db_session) == 1


# --------------------------------------------------------------------------- #
# Producer failure
# --------------------------------------------------------------------------- #


def test_producer_failure_returns_failed_to_produce_review(db_session):
    producer = _RaisingProducer()
    result = _run(db_session, producer=producer)
    assert result.status is MachineReviewOrchestrationStatus.failed_to_produce_review
    assert result.appended_review_id is None
    assert producer.calls == 1  # the producer was reached for a run_* plan


def test_producer_failure_appends_no_row(db_session):
    _run(db_session, producer=_RaisingProducer())
    assert _count_rows(db_session) == 0


def test_producer_invalid_output_returns_failed_to_produce_review(db_session):
    """A producer returning a review without reviewed_at is invalid output."""

    class _BadProducer:
        def review_record(self, context, *, reviewed_at):
            return RecordMachineReview(
                record_type=context.record_type,
                record_ref=context.record_ref,
                status=MachineReviewStatus.machine_screened_pass,
                reviewed_at=None,  # invalid for persistence
            )

    result = _run(db_session, producer=_BadProducer())
    assert result.status is MachineReviewOrchestrationStatus.failed_to_produce_review
    assert _count_rows(db_session) == 0


# --------------------------------------------------------------------------- #
# No real provider, non-interference, result shape
# --------------------------------------------------------------------------- #


def test_producer_orchestration_never_calls_real_provider(db_session):
    result = _run(db_session)
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.provider == "fake"
    assert row.model == "fake-test"

    # The producer module imports no real-provider machinery.
    source = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "machine_review"
        / "producer.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("llm_precheck.providers", "openai", "anthropic", "online_api", "local_http"):
        assert forbidden not in source


def test_producer_orchestration_does_not_mutate_submission_status(db_session, _api_test_user):
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="non-interference",
        summary="baseline",
    )
    db_session.flush()
    snapshot = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )
    _run(db_session, source_submission_id=submission.id)
    db_session.refresh(submission)
    assert (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    ) == snapshot


def test_producer_orchestration_result_forbids_mutation_payload_fields():
    field_names = set(MachineReviewOrchestrationResult.model_fields)
    forbidden = ("set_", "mutation", "override", "apply", "is_certified",
                 "benchmark", "review_status", "trust_status", "evidence")
    for token in forbidden:
        assert not any(token in name for name in field_names), token

    with pytest.raises(ValidationError):
        MachineReviewOrchestrationResult(
            status=MachineReviewOrchestrationStatus.skipped_current,
            decision=MachineReviewReReviewDecision.skip_current,
            record_type="kinetics",
            record_id=_RECORD_ID,
            context_hash="a" * 64,
            context_schema_version="v1",
            prompt_version=_PROMPT,
            rubric_versions={},
            set_review_status="approved",  # type: ignore[call-arg]
        )
