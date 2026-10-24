"""The curator queue says which rubric each task's latest stored machine review was made under."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models.common import MachineReviewStatus as DBStatus
from app.db.models.common import SubmissionRecordType
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.services.trust.rubrics import RUBRICS
from tests.api.test_admin_machine_review_curator_tasks import _BASE, _make_task, _new_submission


def _stored_review(db_session: Session, *, record_id: int, rubric_name: str, rubric_version: int, provider=None):
    row = RecordMachineReviewRow(
        record_type=SubmissionRecordType.kinetics,
        record_id=record_id,
        status=DBStatus.machine_screened_warning,
        findings_json=[],
        provider=provider,
        context_hash="0" * 64,
        context_schema_version=1,
        prompt_name="machine_review",
        prompt_version=1,
        rubric_name=rubric_name,
        rubric_version=rubric_version,
        reviewed_at=datetime(2026, 10, 1, 9, 0, 0),
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_task_carries_the_rubric_its_latest_review_was_made_under(client, db_session, login_as, _api_admin_user):
    current = RUBRICS.current("computed_kinetics").version
    submission = _new_submission(db_session, _api_admin_user)
    cur = _make_task(db_session, submission.id, record_id=201, fingerprint="b" * 64)
    old = _make_task(db_session, submission.id, record_id=202, fingerprint="c" * 64)
    zero = _make_task(db_session, submission.id, record_id=203, fingerprint="d" * 64)
    bare = _make_task(db_session, submission.id, record_id=204, fingerprint="e" * 64)
    _stored_review(db_session, record_id=201, rubric_name="computed_kinetics", rubric_version=current)
    _stored_review(db_session, record_id=202, rubric_name="computed_kinetics", rubric_version=current + 5)
    _stored_review(db_session, record_id=203, rubric_name="computed_kinetics", rubric_version=0)
    login_as(_api_admin_user)
    body = client.get(_BASE).json()
    by_id = {item["id"]: item["made_under"] for item in body["items"]}
    assert by_id[cur.id]["state"] == "current" and by_id[cur.id]["assessed_version"] == current
    assert by_id[old.id]["state"] == "stale" and by_id[old.id]["stale_reason"] == "rubric_version"
    assert by_id[zero.id]["state"] == "stale" and by_id[zero.id]["assessed_version"] == 0
    assert by_id[zero.id]["current_version"] == current
    assert by_id[bare.id] is None
    # The single-task read agrees with the list.
    assert client.get(f"{_BASE}/{old.id}").json()["made_under"] == by_id[old.id]


def test_a_scientific_check_row_is_not_the_review_the_task_is_made_under(client, db_session, login_as, _api_admin_user):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id, record_id=301, fingerprint="f" * 64)
    _stored_review(
        db_session, record_id=301, rubric_name="computed_kinetics", rubric_version=1, provider="tckdb.scientific_checks"
    )
    login_as(_api_admin_user)
    assert client.get(f"{_BASE}/{task.id}").json()["made_under"] is None
