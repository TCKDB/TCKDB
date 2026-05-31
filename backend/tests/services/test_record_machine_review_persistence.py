"""Tests for the append-only ``record_machine_review`` persistence slice.

Cover the migration/model (insert, multiple rows per record, a real
``downgrade()``), the row -> :class:`StoredMachineReviewProjection` projection,
the currency classification over persisted rows, the append-only write helper,
and non-interference (the helper writes only to ``record_machine_review``).

No public exposure is involved: this is a private persistence surface
(``backend/docs/specs/record_machine_review_policy.md`` §8).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review import (
    MachineReviewCategory,
    MachineReviewContextDigest,
    MachineReviewCurrencyState,
    MachineReviewFinding,
    MachineReviewSeverity,
    MachineReviewStatus,
    RecordMachineReview,
    classify_record_machine_review_currency_from_rows,
    create_record_machine_review_row,
    stored_projection_from_record_machine_review_row,
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
    status: MachineReviewStatus = MachineReviewStatus.machine_screened_warning,
    findings: tuple[MachineReviewFinding, ...] = (),
    reviewed_at: datetime = _T0,
    audit_event_id: int | None = None,
    record_id: int | None = 9001,
    model: str | None = "fake/model",
    provider: str | None = "FakeProvider",
) -> RecordMachineReview:
    return RecordMachineReview(
        record_type="kinetics",
        record_ref="kin_9001",
        status=status,
        findings=findings,
        model=model,
        provider=provider,
        reviewed_at=reviewed_at,
        audit_event_id=audit_event_id,
        record_id=record_id,
    )


def _finding() -> MachineReviewFinding:
    return MachineReviewFinding(
        severity=MachineReviewSeverity.warning,
        category=MachineReviewCategory.kinetics,
        record_type="kinetics",
        record_ref="9001",
        message="Note mentions tunneling but tunneling_model is null.",
        evidence_keys=("missing_checks.tunneling_model",),
    )


def _insert(db_session, **kwargs) -> RecordMachineReviewRow:
    """Append one row via the write helper with sensible defaults."""
    review = kwargs.pop("review", None) or _review(**{
        k: kwargs.pop(k) for k in list(kwargs) if k in {
            "status", "findings", "reviewed_at", "audit_event_id", "model", "provider"
        }
    })
    return create_record_machine_review_row(
        db_session,
        record_type=kwargs.pop("record_type", "kinetics"),
        record_id=kwargs.pop("record_id", 9001),
        review=review,
        context_digest=kwargs.pop("context_digest", _digest()),
        prompt_version=kwargs.pop("prompt_version", _PROMPT),
        rubric_versions=kwargs.pop("rubric_versions", _RUBRICS),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Migration / model: insert + append-only + downgrade implementation
# --------------------------------------------------------------------------- #


def test_record_machine_review_table_can_insert_minimal_row(db_session):
    """A minimal row inserts and reads back with its defaults applied."""
    row = _insert(db_session)
    db_session.flush()

    fetched = db_session.get(RecordMachineReviewRow, row.id)
    assert fetched is not None
    assert fetched.record_type is SubmissionRecordType.kinetics
    assert fetched.record_id == 9001
    assert fetched.status is MachineReviewStatus.machine_screened_warning
    assert fetched.findings_json == []  # default empty array
    assert fetched.context_hash == _HASH_A
    assert fetched.context_schema_version == "v1"
    assert fetched.created_at is not None  # server default now()


def test_record_machine_review_table_allows_multiple_rows_for_same_record(db_session):
    """The table is append-only: many rows may exist for one record."""
    _insert(db_session, reviewed_at=_T0)
    _insert(db_session, reviewed_at=_T0 + timedelta(hours=1))
    _insert(db_session, reviewed_at=_T0 + timedelta(hours=2))
    db_session.flush()

    count = db_session.scalar(
        select(func.count())
        .select_from(RecordMachineReviewRow)
        .where(
            RecordMachineReviewRow.record_type == SubmissionRecordType.kinetics,
            RecordMachineReviewRow.record_id == 9001,
        )
    )
    assert count == 3


def test_record_machine_review_migration_has_real_downgrade():
    """The migration ships a real downgrade() that drops the table.

    The test DB is rebuilt by ``alembic upgrade head`` per run, so downgrade is
    not executed here (per the slice's guidance); instead we verify the
    revision file implements a non-stub upgrade/downgrade. The file is read by
    path because ``alembic/versions`` is a script directory, not an importable
    package.
    """
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "c9d0e1f2a3b4_add_record_machine_review.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    assert "def downgrade() -> None:" in source
    assert 'op.drop_table("record_machine_review")' in source
    assert "def upgrade() -> None:" in source
    assert 'op.create_table(\n        "record_machine_review"' in source
    # The shared machine_review_status enum is reused, never dropped here.
    assert 'postgresql.ENUM(name="machine_review_status").drop' not in source


# --------------------------------------------------------------------------- #
# Row -> StoredMachineReviewProjection
# --------------------------------------------------------------------------- #


def test_row_projects_to_stored_currency_projection(db_session):
    row = _insert(db_session)
    db_session.flush()
    projection = stored_projection_from_record_machine_review_row(row)

    assert projection.record_type == "kinetics"
    assert projection.record_id == 9001
    assert projection.reviewed_at == _T0
    assert projection.id == row.id


def test_projection_preserves_status(db_session):
    row = _insert(
        db_session,
        review=_review(status=MachineReviewStatus.machine_screened_needs_attention),
    )
    db_session.flush()
    projection = stored_projection_from_record_machine_review_row(row)
    assert projection.status is MachineReviewStatus.machine_screened_needs_attention


def test_projection_preserves_context_hash_and_schema_version(db_session):
    row = _insert(db_session, context_digest=_digest(_HASH_B, "v2"))
    db_session.flush()
    projection = stored_projection_from_record_machine_review_row(row)
    assert projection.context_hash == _HASH_B
    assert projection.context_schema_version == "v2"


def test_projection_preserves_prompt_version(db_session):
    row = _insert(db_session, prompt_version="prompt_v9")
    db_session.flush()
    projection = stored_projection_from_record_machine_review_row(row)
    assert projection.prompt_version == "prompt_v9"


def test_projection_preserves_rubric_versions(db_session):
    rubrics = {"kinetics": "computed_kinetics_v7", "calc": "computed_calculation_v1"}
    row = _insert(db_session, rubric_versions=rubrics)
    db_session.flush()
    projection = stored_projection_from_record_machine_review_row(row)
    assert projection.rubric_versions == rubrics


def test_projection_preserves_source_submission_id(db_session, _api_test_user):
    """The persisted row round-trips source_submission_id (FK to submission)."""
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="persistence test",
        summary="src submission",
    )
    db_session.flush()

    row = _insert(db_session, source_submission_id=submission.id)
    db_session.flush()
    fetched = db_session.get(RecordMachineReviewRow, row.id)
    assert fetched.source_submission_id == submission.id


def test_projection_preserves_source_audit_event_id(db_session, _api_test_user):
    """source_audit_event_id round-trips on the row and into the projection."""
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="persistence test",
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

    row = _insert(db_session, source_audit_event_id=event.id)
    db_session.flush()
    projection = stored_projection_from_record_machine_review_row(row)
    assert projection.source_audit_event_id == event.id


# --------------------------------------------------------------------------- #
# Currency classification over persisted rows
# --------------------------------------------------------------------------- #


def test_classifier_marks_latest_matching_row_current(db_session):
    _insert(db_session, context_digest=_digest(_HASH_A), reviewed_at=_T0)
    db_session.flush()
    rows = list(db_session.scalars(select(RecordMachineReviewRow)))

    result = classify_record_machine_review_currency_from_rows(
        rows,
        current_context=_digest(_HASH_A),
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert result.state is MachineReviewCurrencyState.current


def test_classifier_marks_latest_context_hash_mismatch_stale(db_session):
    _insert(db_session, context_digest=_digest(_HASH_A), reviewed_at=_T0)
    db_session.flush()
    rows = list(db_session.scalars(select(RecordMachineReviewRow)))

    result = classify_record_machine_review_currency_from_rows(
        rows,
        current_context=_digest(_HASH_B),  # evidence changed since the review
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert result.state is MachineReviewCurrencyState.stale


def test_classifier_marks_older_matching_row_historical_when_latest_is_stale(db_session):
    """The latest (stale) row decides state; an older matching row is historical."""
    older = _insert(db_session, context_digest=_digest(_HASH_A), reviewed_at=_T0)
    newer = _insert(
        db_session,
        context_digest=_digest(_HASH_B),
        reviewed_at=_T0 + timedelta(hours=1),
    )
    db_session.flush()
    rows = list(db_session.scalars(select(RecordMachineReviewRow)))

    result = classify_record_machine_review_currency_from_rows(
        rows,
        current_context=_digest(_HASH_A),  # matches the OLDER row, not the latest
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert result.state is MachineReviewCurrencyState.stale
    assert result.active_review.id == newer.id
    assert tuple(h.id for h in result.historical_reviews) == (older.id,)


# --------------------------------------------------------------------------- #
# Write helper: append-only, findings round-trip
# --------------------------------------------------------------------------- #


def test_create_helper_appends_not_updates(db_session):
    """Two helper calls for the same record create two distinct rows."""
    row1 = _insert(db_session, reviewed_at=_T0)
    row2 = _insert(db_session, reviewed_at=_T0 + timedelta(hours=1))
    db_session.flush()

    assert row1.id != row2.id
    total = db_session.scalar(
        select(func.count()).select_from(RecordMachineReviewRow)
    )
    assert total == 2
    # The first row is unchanged (not overwritten by the second).
    assert db_session.get(RecordMachineReviewRow, row1.id).reviewed_at == _T0


def test_create_helper_round_trips_findings_json(db_session):
    """The review's findings serialise to findings_json and read back intact."""
    review = _review(findings=(_finding(),))
    row = _insert(db_session, review=review)
    db_session.flush()

    fetched = db_session.get(RecordMachineReviewRow, row.id)
    assert isinstance(fetched.findings_json, list)
    assert len(fetched.findings_json) == 1
    finding = fetched.findings_json[0]
    assert finding["severity"] == "warning"
    assert finding["category"] == "kinetics"
    assert finding["record_ref"] == "9001"
    assert finding["evidence_keys"] == ["missing_checks.tunneling_model"]


def test_create_helper_requires_reviewed_at(db_session):
    """A review with no reviewed_at cannot be persisted (latest-selection key)."""
    import pytest

    review = _review()
    object.__setattr__(review, "reviewed_at", None)  # frozen dataclass
    with pytest.raises(ValueError):
        create_record_machine_review_row(
            db_session,
            record_type="kinetics",
            record_id=9001,
            review=review,
            context_digest=_digest(),
            prompt_version=_PROMPT,
            rubric_versions=_RUBRICS,
        )


# --------------------------------------------------------------------------- #
# Non-interference: the helper writes only to record_machine_review
# --------------------------------------------------------------------------- #


def test_create_helper_does_not_touch_submissions_or_other_tables(db_session, _api_test_user):
    """Appending a review row leaves submissions and audit events untouched."""
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.kinetics,
        title="non-interference",
        summary="baseline",
    )
    db_session.flush()
    status_before = submission.status
    submission_count_before = db_session.scalar(
        select(func.count()).select_from(Submission)
    )
    audit_count_before = db_session.scalar(
        select(func.count()).select_from(SubmissionAuditEvent)
    )

    _insert(db_session, source_submission_id=submission.id)
    db_session.flush()
    db_session.refresh(submission)

    # The submission and other tables are unchanged by the append.
    assert submission.status == status_before
    assert db_session.scalar(
        select(func.count()).select_from(Submission)
    ) == submission_count_before
    assert db_session.scalar(
        select(func.count()).select_from(SubmissionAuditEvent)
    ) == audit_count_before
