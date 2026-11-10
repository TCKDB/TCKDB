"""The curator queue says what the stored machine review a task came from was made under, by the currency rule."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models.common import MachineReviewStatus as DBStatus
from app.db.models.common import SubmissionActorKind, SubmissionAuditEventKind, SubmissionRecordType
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_PROMPT
from app.services.submission import append_audit_event
from app.services.trust.rubrics import RUBRICS
from tests.api.test_admin_machine_review_curator_tasks import _BASE, _make_task, _new_submission

CURRENT_PROMPT = ACTIVE_MACHINE_REVIEW_PROMPT.version


def _stored_review(
    db_session: Session,
    *,
    record_id: int,
    rubric_name: str = "computed_kinetics",
    rubric_version: int,
    prompt_version: int = CURRENT_PROMPT,
    provider=None,
    source_audit_event_id: int | None = None,
    reviewed_at: datetime = datetime(2026, 10, 1, 9, 0, 0),
):
    row = RecordMachineReviewRow(
        record_type=SubmissionRecordType.kinetics,
        record_id=record_id,
        status=DBStatus.machine_screened_warning,
        findings_json=[],
        provider=provider,
        context_hash="0" * 64,
        context_schema_version=1,
        prompt_name=ACTIVE_MACHINE_REVIEW_PROMPT.name,
        prompt_version=prompt_version,
        rubric_name=rubric_name,
        rubric_version=rubric_version,
        source_audit_event_id=source_audit_event_id,
        reviewed_at=reviewed_at,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _by_id(client):
    return {item["id"]: item["made_under"] for item in client.get(_BASE).json()["items"]}


def test_stamps_are_read_by_the_currency_rule_and_never_called_current(client, db_session, login_as, _api_admin_user):
    current = RUBRICS.current("computed_kinetics").version
    submission = _new_submission(db_session, _api_admin_user)
    fresh = _make_task(db_session, submission.id, record_id=201, fingerprint="b" * 64)
    old_rubric = _make_task(db_session, submission.id, record_id=202, fingerprint="c" * 64)
    zero_rubric = _make_task(db_session, submission.id, record_id=203, fingerprint="d" * 64)
    # The migration's shape: the rubric's latest old version stored as 1, the old prompt as 0.
    old_prompt = _make_task(db_session, submission.id, record_id=204, fingerprint="e" * 64)
    bare = _make_task(db_session, submission.id, record_id=205, fingerprint="f" * 64)
    _stored_review(db_session, record_id=201, rubric_version=current)
    _stored_review(db_session, record_id=202, rubric_version=current + 5)
    _stored_review(db_session, record_id=203, rubric_version=0)
    _stored_review(db_session, record_id=204, rubric_version=current, prompt_version=0)
    login_as(_api_admin_user)
    made = _by_id(client)
    assert made[fresh.id]["state"] == "inputs_unchecked" and made[fresh.id]["inputs_checked"] is False
    assert made[old_rubric.id]["state"] == "stale" and made[old_rubric.id]["stale_reason"] == "rubric_version"
    assert made[zero_rubric.id]["state"] == "stale" and made[zero_rubric.id]["assessed_version"] == 0
    assert made[zero_rubric.id]["current_version"] == current
    assert made[old_prompt.id]["state"] == "stale" and made[old_prompt.id]["stale_reason"] == "prompt_version"
    assert made[bare.id] is None
    assert client.get(f"{_BASE}/{old_rubric.id}").json()["made_under"] == made[old_rubric.id]


def test_a_task_reads_the_review_that_produced_it_not_the_latest(client, db_session, login_as, _api_admin_user):
    current = RUBRICS.current("computed_kinetics").version
    submission = _new_submission(db_session, _api_admin_user)
    events = [
        append_audit_event(
            db_session,
            submission=submission,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            actor_kind=SubmissionActorKind.system,
        )
        for _ in range(2)
    ]
    task = _make_task(db_session, submission.id, record_id=401, fingerprint="1" * 64)
    task.source_audit_event_id = events[0].id
    # The review that produced the task is old; a newer review of the same record is current.
    _stored_review(
        db_session, record_id=401, rubric_version=0, source_audit_event_id=events[0].id, reviewed_at=datetime(2026, 9, 1)
    )
    _stored_review(
        db_session, record_id=401, rubric_version=current, source_audit_event_id=events[1].id, reviewed_at=datetime(2026, 10, 2)
    )
    db_session.flush()
    login_as(_api_admin_user)
    made = client.get(f"{_BASE}/{task.id}").json()["made_under"]
    assert made["state"] == "stale" and made["assessed_version"] == 0
    # A task whose source review is not stored says nothing rather than borrow another review's stamp.
    orphan = _make_task(db_session, submission.id, record_id=402, fingerprint="2" * 64)
    orphan.source_audit_event_id = events[0].id
    db_session.flush()
    assert client.get(f"{_BASE}/{orphan.id}").json()["made_under"] is None


def test_a_scientific_check_row_is_not_the_review_the_task_is_made_under(client, db_session, login_as, _api_admin_user):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id, record_id=301, fingerprint="a" * 64)
    _stored_review(db_session, record_id=301, rubric_version=1, provider="tckdb.scientific_checks")
    login_as(_api_admin_user)
    assert client.get(f"{_BASE}/{task.id}").json()["made_under"] is None
