"""Database-level guarantees of the machine-review job tables."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models.common import (
    AppUserRole,
    MachineReviewJobEventKind,
    MachineReviewJobFailure,
    MachineReviewJobStatus,
    SubmissionActorKind,
    SubmissionAuditEventKind,
)
from app.db.models.machine_review_job import MachineReviewJobEvent
from app.services.submission import append_audit_event
from tests.services._machine_review_job_support import insert_job, make_submission, make_user

S = MachineReviewJobStatus


@pytest.fixture
def seeded(db_session):
    user = make_user(db_session, "constraint-user", AppUserRole.admin)
    submission = make_submission(db_session, user.id)
    event = append_audit_event(
        db_session,
        submission=submission,
        event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
        actor_kind=SubmissionActorKind.llm,
        details_json={},
    )
    db_session.flush()
    return db_session, user, submission, event


def _refused(session, submission, user, **fields):
    with pytest.raises(IntegrityError) as raised:
        with session.begin_nested():
            insert_job(session, submission.id, user.id, **fields)
    return str(raised.value.orig)


def test_two_open_jobs_for_one_submission_are_refused(seeded):
    session, user, submission, _ = seeded
    insert_job(session, submission.id, user.id, status=S.queued)
    assert "ux_machine_review_job_open_submission" in _refused(session, submission, user, status=S.queued)
    assert "ux_machine_review_job_open_submission" in _refused(session, submission, user, status=S.running)


def test_closed_jobs_do_not_hold_the_slot(seeded):
    session, user, submission, event = seeded
    insert_job(session, submission.id, user.id, status=S.queued)
    insert_job(session, submission.id, user.id, status=S.succeeded, audit_event_id=event.id)
    insert_job(session, submission.id, user.id, status=S.cancelled)


def test_succeeded_requires_an_audit_event(seeded):
    session, user, submission, event = seeded
    assert "audit_event_state" in _refused(session, submission, user, status=S.succeeded)
    assert insert_job(session, submission.id, user.id, status=S.succeeded, audit_event_id=event.id).id


def test_an_open_job_must_not_carry_an_audit_event(seeded):
    session, user, submission, event = seeded
    assert "audit_event_state" in _refused(session, submission, user, status=S.queued, audit_event_id=event.id)


def test_failed_requires_a_failure_code(seeded):
    session, user, submission, _ = seeded
    assert "failure_code_state" in _refused(session, submission, user, status=S.failed)
    assert insert_job(
        session, submission.id, user.id, status=S.failed, failure_code=MachineReviewJobFailure.worker_lost
    ).id


def test_cancelled_allows_only_the_disabled_code(seeded):
    session, user, submission, _ = seeded
    bad = _refused(
        session, submission, user, status=S.cancelled, failure_code=MachineReviewJobFailure.internal_error
    )
    assert "failure_code_state" in bad
    assert insert_job(
        session, submission.id, user.id, status=S.cancelled, failure_code=MachineReviewJobFailure.machine_review_disabled
    ).id


def test_settings_row_is_a_singleton(db_session):
    db_session.execute(text("DELETE FROM machine_review_settings"))
    with pytest.raises(IntegrityError) as raised:
        with db_session.begin_nested():
            db_session.execute(text("INSERT INTO machine_review_settings (id) VALUES (2)"))
    assert "ck_machine_review_settings_singleton" in str(raised.value.orig)
    db_session.execute(text("INSERT INTO machine_review_settings (id) VALUES (1)"))


def test_job_event_sequence_is_unique_per_job(seeded):
    session, user, submission, _ = seeded
    job = insert_job(session, submission.id, user.id)
    session.add(MachineReviewJobEvent(job_id=job.id, seq=1, kind=MachineReviewJobEventKind.claimed))
    session.flush()
    with pytest.raises(IntegrityError) as raised:
        with session.begin_nested():
            session.add(MachineReviewJobEvent(job_id=job.id, seq=1, kind=MachineReviewJobEventKind.finished))
            session.flush()
    assert "uq_machine_review_job_event_job_id_seq" in str(raised.value.orig)
    session.add(MachineReviewJobEvent(job_id=job.id, seq=2, kind=MachineReviewJobEventKind.finished))
    session.flush()
