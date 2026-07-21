"""Tests for explicit append-only reproducibility assessments."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models.common import (
    ReproducibilityAssessorKind,
    ReproducibilityGrade,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.reaction import ChemReaction
from app.db.models.reproducibility_assessment import (
    RecordReproducibilityAssessment,
)
from app.schemas.entities.reproducibility_assessment import (
    ReproducibilityAssessmentRead,
)
from app.services.reproducibility_assessment import (
    append_reproducibility_assessment,
    get_latest_reproducibility_assessment,
    is_reproducibility_assessment_context_current,
)
from app.services.submission import create_submission

_T0 = datetime(2026, 7, 20, 12, 0, 0)


def _make_target(db_session) -> int:
    target = ChemReaction(reversible=True)
    db_session.add(target)
    db_session.flush()
    return target.id


def _append(
    db_session,
    *,
    record_id: int,
    **overrides,
) -> RecordReproducibilityAssessment:
    values = {
        "record_type": SubmissionRecordType.reaction,
        "record_id": record_id,
        "grade": ReproducibilityGrade.described,
        "rubric_name": "computed_reaction_reproducibility",
        "rubric_version": "v1",
        "context_json": {
            "target": {"public_ref": "rxn_example"},
            "sources": ["calc_example"],
        },
        "passed": [{"check": "target_identity"}],
        "missing": [{"check": "output_artifact"}],
        "warnings": ["software release was not recorded"],
        "assessor_kind": ReproducibilityAssessorKind.system,
        "assessed_at": _T0,
    }
    values.update(overrides)
    return append_reproducibility_assessment(db_session, **values)


def test_append_round_trips_explicit_grade_and_evidence_json(db_session):
    target_id = _make_target(db_session)
    row = _append(db_session, record_id=target_id)

    assert row.id is not None
    assert row.record_type is SubmissionRecordType.reaction
    assert row.grade is ReproducibilityGrade.described
    assert len(row.context_hash) == 64
    assert row.context_json["sources"] == ["calc_example"]
    assert row.passed_json == [{"check": "target_identity"}]
    assert row.missing_json == [{"check": "output_artifact"}]
    assert row.warnings_json == ["software release was not recorded"]
    assert row.assessor_user_id is None

    projection = ReproducibilityAssessmentRead.model_validate(row)
    assert projection.context_json == row.context_json
    assert projection.passed == row.passed_json
    assert projection.missing == row.missing_json
    assert projection.warnings == row.warnings_json


def test_append_allows_versioned_history_and_latest_is_deterministic(db_session):
    target_id = _make_target(db_session)
    first = _append(
        db_session,
        record_id=target_id,
        grade="auditable",
    )
    second = _append(
        db_session,
        record_id=target_id,
        grade="rerunnable",
        rubric_version="v2",
        context_json={"inputs": ["job.in"], "environment": {"image": "sha256:abc"}},
        assessed_at=_T0 + timedelta(hours=1),
    )

    assert first.id != second.id
    count = db_session.scalar(
        select(func.count())
        .select_from(RecordReproducibilityAssessment)
        .where(
            RecordReproducibilityAssessment.record_type == SubmissionRecordType.reaction,
            RecordReproducibilityAssessment.record_id == target_id,
        )
    )
    assert count == 2

    latest = get_latest_reproducibility_assessment(
        db_session,
        record_type="reaction",
        record_id=target_id,
    )
    assert latest is not None
    assert latest.id == second.id
    assert latest.grade is ReproducibilityGrade.rerunnable
    assert first.grade is ReproducibilityGrade.auditable


def test_latest_uses_id_as_equal_timestamp_tiebreak(db_session):
    target_id = _make_target(db_session)
    first = _append(db_session, record_id=target_id)
    second = _append(db_session, record_id=target_id, grade="rerunnable")

    latest = get_latest_reproducibility_assessment(
        db_session,
        record_type=SubmissionRecordType.reaction,
        record_id=target_id,
    )
    assert latest is not None
    assert latest.id == second.id
    assert latest.id > first.id


def test_latest_filters_record_address_and_returns_none(db_session):
    selected_id = _make_target(db_session)
    other_id = _make_target(db_session)
    _append(db_session, record_id=selected_id)
    _append(db_session, record_id=other_id)

    latest = get_latest_reproducibility_assessment(
        db_session,
        record_type="reaction",
        record_id=selected_id,
    )
    assert latest is not None
    assert latest.record_type is SubmissionRecordType.reaction
    assert latest.record_id == selected_id
    assert (
        get_latest_reproducibility_assessment(
            db_session,
            record_type="reaction",
            record_id=9999,
        )
        is None
    )


def test_curator_and_optional_submission_attribution(
    db_session,
    _api_test_user,
    _api_curator_user,
):
    target_id = _make_target(db_session)
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.thermo,
        title="reproducibility source",
    )
    row = _append(
        db_session,
        record_id=target_id,
        assessor_kind="curator",
        assessor_user_id=_api_curator_user,
        source_submission_id=submission.id,
    )

    assert row.assessor_kind is ReproducibilityAssessorKind.curator
    assert row.assessor_user_id == _api_curator_user
    assert row.source_submission_id == submission.id


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"assessor_kind": "curator", "assessor_user_id": None},
            "curator assessments require assessor_user_id",
        ),
        (
            {"assessor_kind": "system", "assessor_user_id": 1},
            "system assessments must not set assessor_user_id",
        ),
        ({"expected_context_hash": "not-a-sha256"}, "expected_context_hash"),
    ],
)
def test_append_rejects_invalid_attribution_and_context(overrides, message, db_session):
    target_id = _make_target(db_session)
    with pytest.raises(ValidationError, match=message):
        _append(db_session, record_id=target_id, **overrides)


def test_context_hash_is_stable_across_object_key_order(db_session):
    target_id = _make_target(db_session)
    first = _append(
        db_session,
        record_id=target_id,
        context_json={"b": {"y": 2, "x": 1}, "a": [3, 4]},
    )
    second = _append(
        db_session,
        record_id=target_id,
        context_json={"a": [3, 4], "b": {"x": 1, "y": 2}},
    )
    assert first.context_hash == second.context_hash
    assert first.context_json == second.context_json


def test_context_currency_compares_canonical_current_evidence(db_session):
    target_id = _make_target(db_session)
    row = _append(
        db_session,
        record_id=target_id,
        context_json={"b": {"y": 2, "x": 1}, "a": [3, 4]},
    )

    assert is_reproducibility_assessment_context_current(
        row,
        current_context_json={"a": [3, 4], "b": {"x": 1, "y": 2}},
    )
    assert not is_reproducibility_assessment_context_current(
        row,
        current_context_json={"a": [3, 4], "b": {"x": 1, "y": 3}},
    )


def test_expected_context_hash_must_match_computed_digest(db_session):
    target_id = _make_target(db_session)
    with pytest.raises(ValueError, match="does not match"):
        _append(
            db_session,
            record_id=target_id,
            expected_context_hash="f" * 64,
        )


def test_append_rejects_missing_polymorphic_target(db_session):
    with pytest.raises(ValueError, match="reaction record 999999 does not exist"):
        _append(db_session, record_id=999999)


def test_timezone_aware_assessed_at_is_stored_as_naive_utc(db_session):
    target_id = _make_target(db_session)
    aware = datetime(2026, 7, 20, 15, 0, tzinfo=timezone(timedelta(hours=3)))
    row = _append(db_session, record_id=target_id, assessed_at=aware)
    assert row.assessed_at == _T0
    assert row.assessed_at.tzinfo is None


def test_append_rejects_materially_future_assessed_at(db_session):
    target_id = _make_target(db_session)
    materially_future = datetime.now(timezone.utc) + timedelta(minutes=6)

    with pytest.raises(ValueError, match="materially in the future"):
        _append(db_session, record_id=target_id, assessed_at=materially_future)


def test_database_trigger_rejects_update(db_session):
    target_id = _make_target(db_session)
    row = _append(db_session, record_id=target_id)

    with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
        db_session.execute(
            text("UPDATE record_reproducibility_assessment SET rubric_version = 'tampered' WHERE id = :id"),
            {"id": row.id},
        )

    db_session.expire_all()
    assert db_session.get(RecordReproducibilityAssessment, row.id).rubric_version == "v1"


def test_database_trigger_rejects_delete(db_session):
    target_id = _make_target(db_session)
    row = _append(db_session, record_id=target_id)
    row_id = row.id

    with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
        db_session.execute(
            text("DELETE FROM record_reproducibility_assessment WHERE id = :id"),
            {"id": row_id},
        )

    db_session.expire_all()
    assert db_session.get(RecordReproducibilityAssessment, row_id) is not None


def test_migration_is_direct_child_of_artifact_integrity_revision():
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[2] / "alembic" / "versions" / "b4e8c1f6a2d9_add_reproducibility_assessments.py"
    ).read_text()
    assert 'down_revision: Union[str, Sequence[str], None] = "a7c9e2f4b6d8"' in migration
    assert "BEFORE UPDATE OR DELETE ON record_reproducibility_assessment" in migration
    assert 'op.drop_table("record_reproducibility_assessment")' in migration
