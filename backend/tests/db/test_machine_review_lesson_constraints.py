"""The lesson table's own constraints: text length, status stamps, and what a deleted source leaves behind."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.models.common import (
    AppUserRole,
    MachineReviewCuratorTaskState,
    MachineReviewLessonSignal,
    MachineReviewLessonStatus,
    MachineReviewProviderKey,
)
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.machine_review_job import MachineReviewJob
from app.db.models.machine_review_lesson import MachineReviewLesson
from app.services.machine_review import jobs
from tests.services._machine_review_job_support import FAKE_SETTINGS, make_submission, make_user

NOW = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture
def admin(db_session):
    return make_user(db_session, "lesson-constraint-admin", AppUserRole.admin)


def _lesson(admin, **kw) -> MachineReviewLesson:
    values = {"text": "Fine.", "signal": MachineReviewLessonSignal.curator_flagged_missed_issue, "created_by": admin.id}
    values.update(kw)
    return MachineReviewLesson(**values)


def _count(session) -> int:
    return session.scalar(select(func.count()).select_from(MachineReviewLesson))


def _refused(session, lesson) -> None:
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(lesson)
        session.flush()


def test_text_of_501_characters_is_refused_and_500_is_accepted(db_session, admin):
    _refused(db_session, _lesson(admin, text="x" * 501))
    assert _count(db_session) == 0
    db_session.add(_lesson(admin, text="x" * 500))
    db_session.flush()
    assert _count(db_session) == 1


def test_whitespace_only_text_is_refused_and_a_non_blank_twin_is_accepted(db_session, admin):
    _refused(db_session, _lesson(admin, text="   \n"))
    assert _count(db_session) == 0
    db_session.add(_lesson(admin, text=" a "))
    db_session.flush()
    assert _count(db_session) == 1


def test_a_verified_lesson_needs_its_stamps(db_session, admin):
    _refused(db_session, _lesson(admin, status=MachineReviewLessonStatus.verified))
    assert _count(db_session) == 0
    db_session.add(
        _lesson(admin, status=MachineReviewLessonStatus.verified, verified_by=admin.id, verified_at=NOW)
    )
    db_session.flush()
    assert _count(db_session) == 1


def test_a_proposed_lesson_carries_no_stamps_and_a_retired_one_needs_retire_stamps(db_session, admin):
    _refused(db_session, _lesson(admin, verified_by=admin.id, verified_at=NOW))
    _refused(db_session, _lesson(admin, status=MachineReviewLessonStatus.retired))
    assert _count(db_session) == 0
    db_session.add(_lesson(admin, status=MachineReviewLessonStatus.retired, retired_by=admin.id, retired_at=NOW))
    db_session.flush()
    assert _count(db_session) == 1


def test_deleting_the_source_task_keeps_the_lesson_with_a_null_link(db_session, admin):
    submission = make_submission(db_session, admin.id)
    task = MachineReviewCuratorTask(
        submission_id=submission.id,
        record_type="kinetics",
        record_id=1,
        finding_fingerprint="a" * 64,
        workflow_state=MachineReviewCuratorTaskState.needs_curator_review,
        machine_review_status="machine_screened_warning",
        highest_severity="warning",
        findings_count=1,
    )
    db_session.add(task)
    db_session.flush()
    lesson = _lesson(admin, source_curator_task_id=task.id)
    db_session.add(lesson)
    db_session.flush()
    assert _count(db_session) == 1 and lesson.source_curator_task_id == task.id

    db_session.execute(delete(MachineReviewCuratorTask).where(MachineReviewCuratorTask.id == task.id))
    db_session.refresh(lesson)
    assert _count(db_session) == 1
    assert lesson.source_curator_task_id is None


def test_deleting_the_source_job_keeps_the_lesson_with_a_null_link(db_session, admin):
    submission = make_submission(db_session, admin.id)
    job = jobs.create_job(
        db_session,
        submission_id=submission.id,
        requester=admin,
        provider_key=MachineReviewProviderKey.fake,
        settings_obj=FAKE_SETTINGS,
    )
    lesson = _lesson(admin, source_job_id=job.id)
    db_session.add(lesson)
    db_session.flush()
    assert lesson.source_job_id == job.id

    db_session.execute(delete(MachineReviewJob).where(MachineReviewJob.id == job.id))
    db_session.refresh(lesson)
    assert _count(db_session) == 1
    assert lesson.source_job_id is None
