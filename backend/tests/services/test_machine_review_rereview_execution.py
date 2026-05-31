"""Tests for the private machine-review re-review execution path.

Prove the execution half (policy ``record_machine_review_policy.md`` §5):
``skip_current`` appends nothing; ``run_not_reviewed`` / ``run_stale`` append
exactly one row through the existing helper, stamped with the plan's currency
key; an idempotency guard re-checks currency and skips when the record is
already current; nothing outside ``record_machine_review`` is mutated.

This slice only *persists* a supplied review — it never produces one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db.models.common import SubmissionActorKind, SubmissionAuditEventKind, SubmissionKind
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review import (
    MachineReviewContextDigest,
    MachineReviewCurrencyState,
    MachineReviewReReviewDecision,
    MachineReviewReReviewExecutionResult,
    MachineReviewReReviewExecutionStatus,
    MachineReviewReReviewPlan,
    MachineReviewStatus,
    RecordMachineReview,
    create_record_machine_review_row,
    execute_record_machine_rereview_plan,
    plan_record_machine_rereview,
)
from app.services.submission import create_submission

_T0 = datetime(2026, 5, 31, 12, 0, 0)
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_PROMPT = "prompt_v3"
_RUBRICS = {"kinetics": "computed_kinetics_v1"}


def _digest(context_hash: str = _HASH_A, schema_version: str = "v1"):
    return MachineReviewContextDigest(
        context_hash=context_hash, context_schema_version=schema_version
    )


def _review(
    *,
    reviewed_at: datetime = _T0,
    status: MachineReviewStatus = MachineReviewStatus.machine_screened_warning,
    record_id: int = 9001,
) -> RecordMachineReview:
    return RecordMachineReview(
        record_type="kinetics",
        record_ref=str(record_id),
        status=status,
        reviewed_at=reviewed_at,
        record_id=record_id,
    )


def _plan(
    decision: MachineReviewReReviewDecision,
    *,
    context_hash: str = _HASH_A,
    schema_version: str = "v1",
    prompt_version: str = _PROMPT,
    rubric_versions: dict[str, str] | None = None,
) -> MachineReviewReReviewPlan:
    return MachineReviewReReviewPlan(
        decision=decision,
        # currency_state is descriptive metadata on the plan; the executor keys
        # off `decision` and re-checks live currency itself, so any value is fine
        # for these hand-built plans.
        currency_state=MachineReviewCurrencyState.not_run,
        current_context_hash=context_hash,
        context_schema_version=schema_version,
        prompt_version=prompt_version,
        rubric_versions=rubric_versions if rubric_versions is not None else dict(_RUBRICS),
    )


def _seed_row(db_session, *, context_hash=_HASH_A, reviewed_at=_T0, record_id=9001):
    """Append one persisted row directly (test setup, bypassing execution)."""
    row = create_record_machine_review_row(
        db_session,
        record_type="kinetics",
        record_id=record_id,
        review=_review(reviewed_at=reviewed_at, record_id=record_id),
        context_digest=_digest(context_hash),
        prompt_version=_PROMPT,
        rubric_versions=_RUBRICS,
    )
    db_session.flush()
    return row


def _execute(db_session, plan, *, record_id=9001, review=None, **kwargs):
    return execute_record_machine_rereview_plan(
        db_session,
        record_type="kinetics",
        record_id=record_id,
        plan=plan,
        review=review or _review(record_id=record_id),
        **kwargs,
    )


def _count_rows(db_session, record_id=9001) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(RecordMachineReviewRow)
        .where(RecordMachineReviewRow.record_id == record_id)
    )


# --------------------------------------------------------------------------- #
# Decision gating
# --------------------------------------------------------------------------- #


def test_execute_skips_when_plan_is_skip_current(db_session):
    result = _execute(db_session, _plan(MachineReviewReReviewDecision.skip_current))
    assert result.status is MachineReviewReReviewExecutionStatus.skipped_current
    assert result.appended_review_id is None
    assert _count_rows(db_session) == 0


def test_execute_appends_when_plan_is_run_not_reviewed(db_session):
    result = _execute(db_session, _plan(MachineReviewReReviewDecision.run_not_reviewed))
    assert result.status is MachineReviewReReviewExecutionStatus.appended
    assert result.appended_review_id is not None
    assert _count_rows(db_session) == 1


def test_execute_appends_when_plan_is_run_stale(db_session):
    # A pre-existing row with a different hash makes the record stale for _HASH_B.
    _seed_row(db_session, context_hash=_HASH_A)
    result = _execute(
        db_session,
        _plan(MachineReviewReReviewDecision.run_stale, context_hash=_HASH_B),
        review=_review(reviewed_at=_T0 + timedelta(hours=1)),
    )
    assert result.status is MachineReviewReReviewExecutionStatus.appended
    assert _count_rows(db_session) == 2


# --------------------------------------------------------------------------- #
# Stored currency key comes from the plan
# --------------------------------------------------------------------------- #


def test_execute_uses_plan_context_hash_and_schema_version(db_session):
    plan = _plan(
        MachineReviewReReviewDecision.run_not_reviewed,
        context_hash=_HASH_B,
        schema_version="v2",
    )
    result = _execute(db_session, plan)
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.context_hash == _HASH_B
    assert row.context_schema_version == "v2"
    assert result.context_hash == _HASH_B
    assert result.context_schema_version == "v2"


def test_execute_uses_plan_prompt_version_and_rubric_versions(db_session):
    rubrics = {"kinetics": "computed_kinetics_v9"}
    plan = _plan(
        MachineReviewReReviewDecision.run_not_reviewed,
        prompt_version="prompt_v9",
        rubric_versions=rubrics,
    )
    result = _execute(db_session, plan)
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.prompt_version == "prompt_v9"
    assert row.rubric_versions_json == rubrics
    assert result.prompt_version == "prompt_v9"
    assert result.rubric_versions == rubrics


# --------------------------------------------------------------------------- #
# Provenance preservation
# --------------------------------------------------------------------------- #


def test_execute_preserves_source_submission_id(db_session, _api_test_user):
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="exec test",
        summary="src submission",
    )
    db_session.flush()
    result = _execute(
        db_session,
        _plan(MachineReviewReReviewDecision.run_not_reviewed),
        source_submission_id=submission.id,
    )
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.source_submission_id == submission.id


def test_execute_preserves_source_audit_event_id(db_session, _api_test_user):
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="exec test",
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
    result = _execute(
        db_session,
        _plan(MachineReviewReReviewDecision.run_not_reviewed),
        source_audit_event_id=event.id,
    )
    row = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert row.source_audit_event_id == event.id


def test_execute_flushes_row_visible_in_session(db_session):
    """The appended row is flushed (id populated, queryable) within the session."""
    result = _execute(db_session, _plan(MachineReviewReReviewDecision.run_not_reviewed))
    assert result.appended_review_id is not None
    # Visible to a fresh query in the same session without an explicit commit.
    fetched = db_session.get(RecordMachineReviewRow, result.appended_review_id)
    assert fetched is not None


# --------------------------------------------------------------------------- #
# Idempotency guard
# --------------------------------------------------------------------------- #


def test_execute_is_idempotent_when_same_plan_already_current(db_session):
    """A run_* plan whose recipe is already current at execution time skips."""
    # The record is already current for _HASH_A.
    _seed_row(db_session, context_hash=_HASH_A)
    # A stale-looking plan, but its recipe (_HASH_A) matches the persisted row.
    plan = _plan(MachineReviewReReviewDecision.run_stale, context_hash=_HASH_A)
    result = _execute(
        db_session, plan, review=_review(reviewed_at=_T0 + timedelta(hours=1))
    )
    assert result.status is MachineReviewReReviewExecutionStatus.skipped_current
    assert result.appended_review_id is None
    assert _count_rows(db_session) == 1  # no second row


def test_execute_does_not_append_twice_for_same_unchanged_recipe(db_session):
    """Re-executing the same plan after a successful append is a no-op."""
    plan = _plan(MachineReviewReReviewDecision.run_not_reviewed, context_hash=_HASH_A)

    first = _execute(db_session, plan, review=_review(reviewed_at=_T0))
    assert first.status is MachineReviewReReviewExecutionStatus.appended
    assert _count_rows(db_session) == 1

    # The append made the record current for _HASH_A; re-running the same plan
    # now skips via the idempotency guard.
    second = _execute(db_session, plan, review=_review(reviewed_at=_T0 + timedelta(hours=1)))
    assert second.status is MachineReviewReReviewExecutionStatus.skipped_current
    assert _count_rows(db_session) == 1


# --------------------------------------------------------------------------- #
# Non-interference and result shape
# --------------------------------------------------------------------------- #


def test_execute_does_not_mutate_submission_status(db_session, _api_test_user):
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

    _execute(
        db_session,
        _plan(MachineReviewReReviewDecision.run_not_reviewed),
        source_submission_id=submission.id,
    )
    db_session.refresh(submission)

    assert (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    ) == snapshot


def test_execute_result_forbids_mutation_payload_fields():
    """The result carries no mutation field and rejects injected ones."""
    field_names = set(MachineReviewReReviewExecutionResult.model_fields)
    forbidden = ("set_", "mutation", "override", "apply", "is_certified",
                 "benchmark", "review_status", "trust_status", "evidence")
    for token in forbidden:
        assert not any(token in name for name in field_names), token

    with pytest.raises(ValidationError):
        MachineReviewReReviewExecutionResult(
            status=MachineReviewReReviewExecutionStatus.skipped_current,
            decision=MachineReviewReReviewDecision.skip_current,
            record_type="kinetics",
            record_id=9001,
            context_hash=_HASH_A,
            context_schema_version="v1",
            prompt_version=_PROMPT,
            rubric_versions={},
            set_review_status="approved",  # type: ignore[call-arg]
        )


def test_execute_end_to_end_with_planner(db_session):
    """The planner + executor compose: a fresh record plans run_not_reviewed,
    executes an append, and re-planning then yields skip_current."""
    digest = _digest(_HASH_A)
    plan = plan_record_machine_rereview(
        db_session,
        record_type="kinetics",
        record_id=9001,
        current_context=digest,
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert plan.decision is MachineReviewReReviewDecision.run_not_reviewed

    result = _execute(db_session, plan, review=_review(reviewed_at=_T0))
    assert result.status is MachineReviewReReviewExecutionStatus.appended

    replan = plan_record_machine_rereview(
        db_session,
        record_type="kinetics",
        record_id=9001,
        current_context=digest,
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert replan.decision is MachineReviewReReviewDecision.skip_current
