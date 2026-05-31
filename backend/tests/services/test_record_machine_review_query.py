"""Tests for the private persisted machine-review query service.

Cover the read path over ``record_machine_review`` rows: filtering by
``(record_type, record_id)``, newest-first ordering matching the classifier's
latest-selection policy (``reviewed_at`` DESC, ``source_audit_event_id`` DESC
NULLS LAST, ``id`` DESC NULLS LAST), ``limit`` after ordering, currency
classification over persisted rows, and read-only non-interference.

This is a private surface (``backend/docs/specs/record_machine_review_policy.md``
§4/§8); no public exposure is involved.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
)
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review import (
    MachineReviewContextDigest,
    MachineReviewCurrencyState,
    MachineReviewStatus,
    RecordMachineReview,
    create_record_machine_review_row,
    get_latest_record_machine_review_row,
    get_record_machine_review_currency_for_record,
    list_record_machine_review_rows_for_record,
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
    record_type: str = "kinetics",
    record_id: int | None = 9001,
    audit_event_id: int | None = None,
) -> RecordMachineReview:
    return RecordMachineReview(
        record_type=record_type,
        record_ref=str(record_id),
        status=MachineReviewStatus.machine_screened_warning,
        reviewed_at=reviewed_at,
        audit_event_id=audit_event_id,
        record_id=record_id,
    )


def _insert(
    db_session,
    *,
    record_type: str = "kinetics",
    record_id: int = 9001,
    reviewed_at: datetime = _T0,
    context_digest: MachineReviewContextDigest | None = None,
    source_audit_event_id: int | None = None,
) -> RecordMachineReviewRow:
    row = create_record_machine_review_row(
        db_session,
        record_type=record_type,
        record_id=record_id,
        review=_review(reviewed_at=reviewed_at, record_type=record_type, record_id=record_id),
        context_digest=context_digest or _digest(),
        prompt_version=_PROMPT,
        rubric_versions=_RUBRICS,
        source_audit_event_id=source_audit_event_id,
    )
    db_session.flush()
    return row


def _make_audit_event(db_session, user_id: int) -> int:
    """Create a real submission + llm_precheck audit event; return its id.

    Needed only where a non-null ``source_audit_event_id`` FK is required to
    exercise NULLS-LAST ordering.
    """
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.kinetics,
        title="query test",
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
    return event.id


# --------------------------------------------------------------------------- #
# Filtering and ordering
# --------------------------------------------------------------------------- #


def test_query_rows_for_record_returns_newest_first(db_session):
    """Rows come back ordered newest-first by reviewed_at."""
    _insert(db_session, reviewed_at=_T0)
    _insert(db_session, reviewed_at=_T0 + timedelta(hours=2))
    _insert(db_session, reviewed_at=_T0 + timedelta(hours=1))

    rows = list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=9001
    )
    reviewed = [r.reviewed_at for r in rows]
    assert reviewed == sorted(reviewed, reverse=True)
    assert reviewed[0] == _T0 + timedelta(hours=2)


def test_query_rows_for_record_filters_by_record_type(db_session):
    """Only rows of the requested record_type are returned."""
    _insert(db_session, record_type="kinetics", record_id=9001)
    _insert(db_session, record_type="thermo", record_id=9001)

    rows = list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=9001
    )
    assert len(rows) == 1
    assert rows[0].record_type.value == "kinetics"


def test_query_rows_for_record_filters_by_record_id(db_session):
    """Only rows of the requested record_id are returned."""
    _insert(db_session, record_type="kinetics", record_id=9001)
    _insert(db_session, record_type="kinetics", record_id=9002)

    rows = list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=9001
    )
    assert len(rows) == 1
    assert rows[0].record_id == 9001


def test_query_rows_for_record_returns_empty_for_no_rows(db_session):
    """A record with no rows yields an empty list (not an error)."""
    rows = list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=123456
    )
    assert rows == []


def test_query_rows_for_record_limit_applies_after_ordering(db_session):
    """limit selects the newest N rows, in order."""
    _insert(db_session, reviewed_at=_T0)
    newest = _insert(db_session, reviewed_at=_T0 + timedelta(hours=2))
    middle = _insert(db_session, reviewed_at=_T0 + timedelta(hours=1))

    one = list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=9001, limit=1
    )
    assert [r.id for r in one] == [newest.id]

    two = list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=9001, limit=2
    )
    assert [r.id for r in two] == [newest.id, middle.id]


def test_query_order_matches_classifier_latest_policy_reviewed_at(db_session):
    """Primary ordering is reviewed_at DESC."""
    old = _insert(db_session, reviewed_at=_T0)
    new = _insert(db_session, reviewed_at=_T0 + timedelta(days=1))
    rows = list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=9001
    )
    assert [r.id for r in rows] == [new.id, old.id]


def test_query_order_matches_classifier_latest_policy_source_audit_event_id_nulls_last(
    db_session, _api_test_user
):
    """At equal reviewed_at, a real source_audit_event_id outranks NULL (NULLS LAST)."""
    event_id = _make_audit_event(db_session, _api_test_user)
    with_null = _insert(db_session, reviewed_at=_T0, source_audit_event_id=None)
    with_event = _insert(db_session, reviewed_at=_T0, source_audit_event_id=event_id)

    rows = list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=9001
    )
    # The row WITH an audit-event id is newest; the NULL one sorts last.
    assert rows[0].id == with_event.id
    assert rows[-1].id == with_null.id


def test_query_order_matches_classifier_latest_policy_id_nulls_last(db_session):
    """At equal reviewed_at and source_audit_event_id, higher id is newest (DESC).

    ``id`` is a non-null PK, so this exercises the final id-DESC tiebreak (the
    NULLS-LAST clause is a no-op for a non-null column but kept for parity).
    """
    first = _insert(db_session, reviewed_at=_T0, source_audit_event_id=None)
    second = _insert(db_session, reviewed_at=_T0, source_audit_event_id=None)
    assert second.id > first.id

    rows = list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=9001
    )
    assert [r.id for r in rows] == [second.id, first.id]


def test_get_latest_row_returns_the_newest(db_session):
    """The convenience latest helper returns the single newest row, or None."""
    assert (
        get_latest_record_machine_review_row(
            db_session, record_type="kinetics", record_id=9001
        )
        is None
    )
    _insert(db_session, reviewed_at=_T0)
    newest = _insert(db_session, reviewed_at=_T0 + timedelta(hours=3))
    latest = get_latest_record_machine_review_row(
        db_session, record_type="kinetics", record_id=9001
    )
    assert latest.id == newest.id


# --------------------------------------------------------------------------- #
# Currency classification over persisted rows
# --------------------------------------------------------------------------- #


def _currency(db_session, *, current_hash=_HASH_A, record_id=9001):
    return get_record_machine_review_currency_for_record(
        db_session,
        record_type="kinetics",
        record_id=record_id,
        current_context=_digest(current_hash),
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )


def test_get_currency_for_record_returns_not_run_when_no_rows(db_session):
    result = _currency(db_session, record_id=424242)
    assert result.state is MachineReviewCurrencyState.not_run
    assert result.active_review is None


def test_get_currency_for_record_returns_current_for_matching_latest_row(db_session):
    _insert(db_session, reviewed_at=_T0, context_digest=_digest(_HASH_A))
    result = _currency(db_session, current_hash=_HASH_A)
    assert result.state is MachineReviewCurrencyState.current


def test_get_currency_for_record_returns_stale_for_mismatched_latest_row(db_session):
    _insert(db_session, reviewed_at=_T0, context_digest=_digest(_HASH_A))
    result = _currency(db_session, current_hash=_HASH_B)  # evidence changed since
    assert result.state is MachineReviewCurrencyState.stale


def test_get_currency_for_record_marks_older_rows_historical(db_session):
    older = _insert(db_session, reviewed_at=_T0, context_digest=_digest(_HASH_A))
    newer = _insert(
        db_session,
        reviewed_at=_T0 + timedelta(hours=1),
        context_digest=_digest(_HASH_B),
    )
    # Active context matches the OLDER row; the latest (newer) row is stale.
    result = _currency(db_session, current_hash=_HASH_A)
    assert result.state is MachineReviewCurrencyState.stale
    assert result.active_review.id == newer.id
    assert tuple(h.id for h in result.historical_reviews) == (older.id,)


# --------------------------------------------------------------------------- #
# Non-interference: read functions mutate nothing
# --------------------------------------------------------------------------- #


def test_query_service_does_not_mutate_submission_status(db_session, _api_test_user):
    """Listing/classifying never touches submission status or approval fields."""
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="non-interference",
        summary="baseline",
    )
    db_session.flush()
    _insert(db_session, reviewed_at=_T0)

    snapshot = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )
    row_count_before = db_session.scalar(
        select(func.count()).select_from(RecordMachineReviewRow)
    )

    list_record_machine_review_rows_for_record(
        db_session, record_type="kinetics", record_id=9001
    )
    _currency(db_session, current_hash=_HASH_A)
    db_session.refresh(submission)

    assert (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    ) == snapshot
    # No rows were inserted or removed by the read path.
    assert db_session.scalar(
        select(func.count()).select_from(RecordMachineReviewRow)
    ) == row_count_before
