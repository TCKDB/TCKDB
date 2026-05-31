"""Tests for the private machine-review re-review decision/planning service.

Prove the trigger policy (policy ``record_machine_review_policy.md`` §5) is
detected correctly from persisted rows: no review -> run_not_reviewed, a
current review -> skip_current, a stale review -> run_stale (with the right
stale reasons), always derived from the *latest* row. Planning is read-only and
appends nothing (the execution slice is separate).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db.models.common import SubmissionKind
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.submission import Submission
from app.services.machine_review import (
    MachineReviewContextDigest,
    MachineReviewCurrencyState,
    MachineReviewReReviewDecision,
    MachineReviewStaleReason,
    MachineReviewStatus,
    RecordMachineReview,
    create_record_machine_review_row,
    plan_record_machine_rereview,
    should_run_machine_rereview,
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


def _insert(
    db_session,
    *,
    reviewed_at: datetime = _T0,
    context_hash: str = _HASH_A,
    schema_version: str = "v1",
    prompt_version: str = _PROMPT,
    rubric_versions: dict[str, str] | None = None,
    record_id: int = 9001,
) -> RecordMachineReviewRow:
    review = RecordMachineReview(
        record_type="kinetics",
        record_ref=str(record_id),
        status=MachineReviewStatus.machine_screened_warning,
        reviewed_at=reviewed_at,
        record_id=record_id,
    )
    row = create_record_machine_review_row(
        db_session,
        record_type="kinetics",
        record_id=record_id,
        review=review,
        context_digest=_digest(context_hash, schema_version),
        prompt_version=prompt_version,
        rubric_versions=rubric_versions if rubric_versions is not None else _RUBRICS,
    )
    db_session.flush()
    return row


def _plan(
    db_session,
    *,
    record_id: int = 9001,
    current_hash: str = _HASH_A,
    schema_version: str = "v1",
    prompt_version: str = _PROMPT,
    rubric_versions: dict[str, str] | None = None,
):
    return plan_record_machine_rereview(
        db_session,
        record_type="kinetics",
        record_id=record_id,
        current_context=_digest(current_hash, schema_version),
        active_prompt_version=prompt_version,
        active_rubric_versions=rubric_versions if rubric_versions is not None else _RUBRICS,
    )


# --------------------------------------------------------------------------- #
# Decision per currency state
# --------------------------------------------------------------------------- #


def test_plan_returns_run_not_reviewed_when_no_rows(db_session):
    plan = _plan(db_session, record_id=777777)
    assert plan.decision is MachineReviewReReviewDecision.run_not_reviewed
    assert plan.currency_state is MachineReviewCurrencyState.not_run
    assert plan.active_review_id is None
    assert plan.stale_reasons == ()


def test_plan_returns_skip_current_when_latest_row_matches_active_recipe(db_session):
    _insert(db_session)
    plan = _plan(db_session)
    assert plan.decision is MachineReviewReReviewDecision.skip_current
    assert plan.currency_state is MachineReviewCurrencyState.current
    assert plan.stale_reasons == ()


def test_plan_returns_run_stale_when_context_hash_mismatch(db_session):
    _insert(db_session, context_hash=_HASH_A)
    plan = _plan(db_session, current_hash=_HASH_B)
    assert plan.decision is MachineReviewReReviewDecision.run_stale
    assert plan.currency_state is MachineReviewCurrencyState.stale
    assert plan.stale_reasons == (MachineReviewStaleReason.context_hash_mismatch,)


def test_plan_returns_run_stale_when_context_schema_version_mismatch(db_session):
    _insert(db_session, schema_version="v1")
    plan = _plan(db_session, schema_version="v2")
    assert plan.decision is MachineReviewReReviewDecision.run_stale
    assert plan.stale_reasons == (
        MachineReviewStaleReason.context_schema_version_mismatch,
    )


def test_plan_returns_run_stale_when_prompt_version_mismatch(db_session):
    _insert(db_session, prompt_version="prompt_v3")
    plan = _plan(db_session, prompt_version="prompt_v4")
    assert plan.decision is MachineReviewReReviewDecision.run_stale
    assert plan.stale_reasons == (MachineReviewStaleReason.prompt_version_mismatch,)


def test_plan_returns_run_stale_when_rubric_versions_mismatch(db_session):
    _insert(db_session, rubric_versions={"kinetics": "computed_kinetics_v1"})
    plan = _plan(db_session, rubric_versions={"kinetics": "computed_kinetics_v2"})
    assert plan.decision is MachineReviewReReviewDecision.run_stale
    assert plan.stale_reasons == (MachineReviewStaleReason.rubric_versions_mismatch,)


# --------------------------------------------------------------------------- #
# Latest-row semantics, reasons ordering, provenance
# --------------------------------------------------------------------------- #


def test_plan_uses_latest_row_not_older_matching_row(db_session):
    """The decision follows the latest row even if an older row matches."""
    _insert(db_session, reviewed_at=_T0, context_hash=_HASH_A)  # older, matches now
    newer = _insert(
        db_session, reviewed_at=_T0 + timedelta(hours=1), context_hash=_HASH_B
    )
    plan = _plan(db_session, current_hash=_HASH_A)
    # Latest row's hash (_HASH_B) != active (_HASH_A) -> stale, keyed on the newer row.
    assert plan.decision is MachineReviewReReviewDecision.run_stale
    assert plan.active_review_id == newer.id


def test_plan_reports_stale_reasons_in_classifier_order(db_session):
    """Multiple mismatches are reported in the classifier's fixed order."""
    _insert(
        db_session,
        context_hash=_HASH_A,
        schema_version="v1",
        prompt_version="prompt_v3",
        rubric_versions={"kinetics": "computed_kinetics_v1"},
    )
    plan = _plan(
        db_session,
        current_hash=_HASH_B,
        schema_version="v2",
        prompt_version="prompt_v4",
        rubric_versions={"kinetics": "computed_kinetics_v2"},
    )
    assert plan.stale_reasons == (
        MachineReviewStaleReason.context_schema_version_mismatch,
        MachineReviewStaleReason.context_hash_mismatch,
        MachineReviewStaleReason.prompt_version_mismatch,
        MachineReviewStaleReason.rubric_versions_mismatch,
    )


def test_plan_preserves_active_review_id_for_current(db_session):
    """A skip_current plan carries the live review's id and the active recipe."""
    row = _insert(db_session)
    plan = _plan(db_session)
    assert plan.decision is MachineReviewReReviewDecision.skip_current
    assert plan.active_review_id == row.id
    assert plan.current_context_hash == _HASH_A
    assert plan.context_schema_version == "v1"
    assert plan.prompt_version == _PROMPT
    assert plan.rubric_versions == _RUBRICS


# --------------------------------------------------------------------------- #
# should_run convenience
# --------------------------------------------------------------------------- #


def test_should_run_machine_rereview_true_for_not_reviewed_and_stale(db_session):
    not_reviewed = _plan(db_session, record_id=888888)
    assert should_run_machine_rereview(not_reviewed) is True

    _insert(db_session, context_hash=_HASH_A)
    stale = _plan(db_session, current_hash=_HASH_B)
    assert should_run_machine_rereview(stale) is True


def test_should_run_machine_rereview_false_for_current(db_session):
    _insert(db_session)
    current = _plan(db_session)
    assert should_run_machine_rereview(current) is False


# --------------------------------------------------------------------------- #
# Non-interference: planning mutates nothing
# --------------------------------------------------------------------------- #


def test_plan_does_not_mutate_existing_rows(db_session, _api_test_user):
    """Planning never appends/updates rows or touches submissions."""
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="rereview non-interference",
        summary="baseline",
    )
    db_session.flush()
    row = _insert(db_session, context_hash=_HASH_A)

    row_count_before = db_session.scalar(
        select(func.count()).select_from(RecordMachineReviewRow)
    )
    row_snapshot = (row.context_hash, row.reviewed_at, row.status, row.prompt_version)
    status_before = submission.status

    # Plan against a changed recipe (would be the trigger to run a review).
    _plan(db_session, current_hash=_HASH_B)
    db_session.refresh(row)
    db_session.refresh(submission)

    # No new row, and the existing row is byte-for-byte unchanged.
    assert db_session.scalar(
        select(func.count()).select_from(RecordMachineReviewRow)
    ) == row_count_before
    assert (
        row.context_hash,
        row.reviewed_at,
        row.status,
        row.prompt_version,
    ) == row_snapshot
    assert submission.status == status_before
