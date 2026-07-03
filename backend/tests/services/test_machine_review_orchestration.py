"""Tests for the private fake/supplied-review machine-review orchestration driver.

Prove the full private loop for one record (policy
``record_machine_review_policy.md`` §5): live trust → context → digest → plan →
fake review → executor → appended row / skipped_current. The driver appends only
through the executor, uses no real provider, is idempotent for an unchanged
recipe, re-appends when evidence changes (stale), and mutates nothing outside
``record_machine_review`` — including the public ``TrustFragment`` it reads.
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
    MachineReviewOrchestrationResult,
    MachineReviewOrchestrationStatus,
    MachineReviewReReviewDecision,
    build_machine_review_context_hash,
    build_machine_review_evidence_context_from_trust,
    run_fake_record_machine_review,
)
from app.services.submission import create_submission
from app.services.trust.fragment import build_trust_fragment
from app.services.trust.models import EvidenceBadge, EvidenceEvaluation

_T0 = datetime(2026, 5, 31, 12, 0, 0)
_T1 = _T0 + timedelta(hours=1)
_T2 = _T0 + timedelta(hours=2)
_PROMPT = "prompt_v3"
_RUBRICS = {"kinetics": "computed_kinetics_v1"}
_RECORD_ID = 9001
_RECORD_REF = "kin_9001"


def _fragment(
    *,
    missing_checks: tuple[str, ...] = ("uncertainty_present",),
    review_status: str = "not_reviewed",
):
    """Build a public trust fragment from a deterministic evaluation."""
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
    return build_trust_fragment(evaluation, review_status=review_status)


def _run(db_session, *, fragment=None, reviewed_at=_T0, record_id=_RECORD_ID, **kwargs):
    return run_fake_record_machine_review(
        db_session,
        record_type="kinetics",
        record_id=record_id,
        record_ref=_RECORD_REF,
        trust_fragment=fragment if fragment is not None else _fragment(),
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
        reviewed_at=reviewed_at,
        **kwargs,
    )


def _count_rows(db_session, record_id=_RECORD_ID) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(RecordMachineReviewRow)
        .where(RecordMachineReviewRow.record_id == record_id)
    )


# --------------------------------------------------------------------------- #
# Append / skip per currency state
# --------------------------------------------------------------------------- #


def test_fake_orchestration_appends_when_not_reviewed(db_session):
    result = _run(db_session)
    assert result.status is MachineReviewOrchestrationStatus.appended
    assert result.decision is MachineReviewReReviewDecision.run_not_reviewed
    assert result.appended_review_id is not None
    assert _count_rows(db_session) == 1


def test_fake_orchestration_appends_when_stale(db_session):
    # First run appends for the original evidence.
    _run(db_session, fragment=_fragment(missing_checks=("uncertainty_present",)), reviewed_at=_T0)
    # Evidence changes -> the latest row is stale -> a second row is appended.
    result = _run(
        db_session,
        fragment=_fragment(missing_checks=()),  # different evidence => different hash
        reviewed_at=_T1,
    )
    assert result.status is MachineReviewOrchestrationStatus.appended
    assert result.decision is MachineReviewReReviewDecision.run_stale
    assert _count_rows(db_session) == 2


def test_fake_orchestration_skips_when_current(db_session):
    _run(db_session, reviewed_at=_T0)  # appends
    # Same evidence/recipe again -> already current -> skip.
    result = _run(db_session, reviewed_at=_T1)
    assert result.status is MachineReviewOrchestrationStatus.skipped_current
    assert result.appended_review_id is None
    assert _count_rows(db_session) == 1


def test_fake_orchestration_is_idempotent_for_unchanged_recipe(db_session):
    first = _run(db_session, reviewed_at=_T0)
    second = _run(db_session, reviewed_at=_T1)
    third = _run(db_session, reviewed_at=_T2)
    assert first.status is MachineReviewOrchestrationStatus.appended
    assert second.status is MachineReviewOrchestrationStatus.skipped_current
    assert third.status is MachineReviewOrchestrationStatus.skipped_current
    assert _count_rows(db_session) == 1


# --------------------------------------------------------------------------- #
# Live context hash / staleness on evidence change
# --------------------------------------------------------------------------- #


def test_fake_orchestration_uses_live_context_hash(db_session):
    fragment = _fragment()
    result = _run(db_session, fragment=fragment, reviewed_at=_T0)

    # The stored row's hash equals the live digest of the same fragment.
    expected = build_machine_review_context_hash(
        build_machine_review_evidence_context_from_trust(
            record_type="kinetics", record_ref=_RECORD_REF, trust_fragment=fragment
        )
    ).context_hash
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.context_hash == expected
    assert result.context_hash == expected


def test_fake_orchestration_stales_when_evidence_changes(db_session):
    r1 = _run(db_session, fragment=_fragment(missing_checks=("uncertainty_present",)), reviewed_at=_T0)
    # Unchanged evidence -> idempotent skip.
    r2 = _run(db_session, fragment=_fragment(missing_checks=("uncertainty_present",)), reviewed_at=_T1)
    # Changed evidence -> stale -> append.
    r3 = _run(db_session, fragment=_fragment(missing_checks=()), reviewed_at=_T2)

    assert r1.status is MachineReviewOrchestrationStatus.appended
    assert r2.status is MachineReviewOrchestrationStatus.skipped_current
    assert r3.status is MachineReviewOrchestrationStatus.appended
    assert r3.decision is MachineReviewReReviewDecision.run_stale
    assert _count_rows(db_session) == 2


# --------------------------------------------------------------------------- #
# Provenance preservation
# --------------------------------------------------------------------------- #


def test_fake_orchestration_preserves_source_submission_id(db_session, _api_test_user):
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="orch test",
        summary="src submission",
    )
    db_session.flush()
    result = _run(db_session, source_submission_id=submission.id)
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.source_submission_id == submission.id


def test_fake_orchestration_preserves_source_audit_event_id(db_session, _api_test_user):
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="orch test",
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


# --------------------------------------------------------------------------- #
# No real provider
# --------------------------------------------------------------------------- #


def test_fake_orchestration_never_calls_real_provider(db_session):
    """Behaviourally: the driver produces a fake-stamped row with no provider.

    The synthesised default review carries the obvious fake provenance, and the
    orchestration module imports no real-provider machinery (asserted from its
    source so the boundary cannot regress).
    """
    result = _run(db_session)
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.provider == "fake"
    assert row.model == "fake-test"

    source = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "machine_review"
        / "orchestration.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("llm_precheck.providers", "openai", "anthropic", "online_api", "local_http"):
        assert forbidden not in source


def test_fake_orchestration_failed_to_produce_review_when_no_review_and_no_clock(db_session):
    """A required review with neither a supplied review nor a clock cannot run."""
    result = run_fake_record_machine_review(
        db_session,
        record_type="kinetics",
        record_id=_RECORD_ID,
        record_ref=_RECORD_REF,
        trust_fragment=_fragment(),
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
        fake_review=None,
        reviewed_at=None,
    )
    assert result.status is MachineReviewOrchestrationStatus.failed_to_produce_review
    assert result.appended_review_id is None
    assert _count_rows(db_session) == 0


# --------------------------------------------------------------------------- #
# Non-interference and result shape
# --------------------------------------------------------------------------- #


def test_fake_orchestration_does_not_mutate_submission_status(db_session, _api_test_user):
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


def test_fake_orchestration_does_not_change_public_trust_fragment_shape(db_session):
    """The driver only reads the TrustFragment; its shape/values are unchanged."""
    fragment = _fragment()
    before = fragment.model_dump()
    _run(db_session, fragment=fragment, reviewed_at=_T0)
    after = fragment.model_dump()
    assert after == before
    # The public fragment still has no machine_review key.
    assert "machine_review" not in after
    assert set(before) == {"review_status", "trust_status", "evidence", "llm_precheck", "is_certified"}


def test_fake_orchestration_result_forbids_mutation_payload_fields():
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
