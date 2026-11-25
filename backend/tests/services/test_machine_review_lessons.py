"""The lesson service: status transitions, what is loaded for a job, and where a lesson's category comes from."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.api.errors import DomainError, NotFoundError
from app.db.models.common import (
    AppUserRole,
    MachineReviewCategory,
    MachineReviewLessonSignal,
    MachineReviewLessonStatus,
    SubmissionRecordType,
)
from app.db.models.machine_review_lesson import MachineReviewLesson
from app.schemas.entities.machine_review_lesson import LessonScope
from app.services.machine_review import lessons
from tests.services._machine_review_job_support import make_user

S = MachineReviewLessonStatus
CODE = "atom_map_atoms_unaccounted_for"
SIGNAL = MachineReviewLessonSignal.curator_flagged_missed_issue


@pytest.fixture
def people(db_session):
    return make_user(db_session, "lesson-curator", AppUserRole.curator), make_user(
        db_session, "lesson-admin", AppUserRole.admin
    )


def _propose(session, author, text="Check the spin multiplicity first.", **kw):
    values = {"author_id": author.id, "text": text, "signal": SIGNAL, "category": MachineReviewCategory.thermo}
    values.update(kw)
    return lessons.propose_lesson(session, **values)


def _keys(session, *types):
    return [e.source_key for e in lessons.verified_lesson_entries(session, record_types=types)]


def test_a_proposed_lesson_is_not_loaded_and_the_same_text_once_verified_is(db_session, people):
    curator, admin = people
    lesson = _propose(db_session, curator)
    assert lesson.status is S.proposed
    assert _keys(db_session, "thermo") == []

    lessons.verify_lesson(db_session, lesson.id, actor_id=admin.id)
    (entry,) = lessons.verified_lesson_entries(db_session, record_types={"thermo"})
    assert entry.source_key == f"lesson:{lesson.id}"
    assert entry.kind == "lesson" and entry.text == "Check the spin multiplicity first."
    assert entry.fields["author"] == "curator"
    assert "lesson-curator" not in repr(entry) and "lesson-admin" not in repr(entry)


def test_verify_on_a_retired_lesson_is_refused_and_verify_on_a_proposed_one_succeeds(db_session, people):
    curator, admin = people
    retired = _propose(db_session, curator)
    lessons.retire_lesson(db_session, retired.id, actor_id=admin.id)
    with pytest.raises(lessons.MachineReviewLessonError) as refused:
        lessons.verify_lesson(db_session, retired.id, actor_id=admin.id)
    assert (refused.value.status, refused.value.code) == (409, "machine_review_lesson_retired")
    assert retired.status is S.retired and retired.verified_by is None

    fresh = _propose(db_session, curator)
    assert lessons.verify_lesson(db_session, fresh.id, actor_id=admin.id).status is S.verified


def test_retiring_a_verified_lesson_removes_it_from_the_entries(db_session, people):
    curator, admin = people
    lesson = _propose(db_session, curator)
    lessons.verify_lesson(db_session, lesson.id, actor_id=admin.id)
    assert len(_keys(db_session, "thermo")) == 1
    lessons.retire_lesson(db_session, lesson.id, actor_id=admin.id)
    assert len(_keys(db_session, "thermo")) == 0
    assert lessons.retire_lesson(db_session, lesson.id, actor_id=admin.id).status is S.retired


def test_editing_a_verified_lesson_keeps_it_verified_and_restamps_the_editor(db_session, people):
    curator, admin = people
    other = make_user(db_session, "lesson-admin-2", AppUserRole.admin)
    lesson = _propose(db_session, curator)
    lessons.verify_lesson(db_session, lesson.id, actor_id=admin.id)
    assert lesson.verified_by == admin.id

    lessons.edit_lesson(db_session, lesson.id, actor_id=other.id, text="Reworded.")
    assert (lesson.status, lesson.verified_by, lesson.text) == (S.verified, other.id, "Reworded.")
    assert [e.text for e in lessons.verified_lesson_entries(db_session, record_types={"thermo"})] == ["Reworded."]

    lessons.retire_lesson(db_session, lesson.id, actor_id=admin.id)
    with pytest.raises(lessons.MachineReviewLessonError):
        lessons.edit_lesson(db_session, lesson.id, actor_id=admin.id, text="Too late.")


def test_text_is_stripped_and_501_characters_or_blank_text_is_refused(db_session, people):
    curator, _ = people
    assert _propose(db_session, curator, text="  padded  ").text == "padded"
    assert _propose(db_session, curator, text="x" * 500)
    for bad in ("x" * 501, "   "):
        with pytest.raises(DomainError):
            _propose(db_session, curator, text=bad)
    assert db_session.scalar(select(func.count()).select_from(MachineReviewLesson)) == 2


def test_an_unknown_lesson_or_source_job_is_a_coded_not_found(db_session, people):
    curator, admin = people
    with pytest.raises(NotFoundError) as lesson_missing:
        lessons.verify_lesson(db_session, 987654321, actor_id=admin.id)
    assert lesson_missing.value.code == "machine_review_lesson_not_found"
    with pytest.raises(NotFoundError) as job_missing:
        _propose(db_session, curator, source_job_id=987654321)
    assert job_missing.value.code == "machine_review_job_not_found"


def test_the_record_type_filter_keeps_matching_and_untyped_lessons_only(db_session, people):
    curator, admin = people
    made = {
        "kinetics": _propose(db_session, curator, text="kinetics lesson", record_type=SubmissionRecordType.kinetics),
        "thermo": _propose(db_session, curator, text="thermo lesson", record_type=SubmissionRecordType.thermo),
        "any": _propose(db_session, curator, text="untyped lesson"),
    }
    for lesson in made.values():
        lessons.verify_lesson(db_session, lesson.id, actor_id=admin.id)
    texts = {e.text for e in lessons.verified_lesson_entries(db_session, record_types={"thermo"})}
    assert texts == {"thermo lesson", "untyped lesson"}
    assert {e.text for e in lessons.verified_lesson_entries(db_session, record_types=set())} == {"untyped lesson"}


def test_the_load_is_newest_first_and_capped(db_session, people):
    curator, admin = people
    made = [_propose(db_session, curator, text=f"lesson number {i}") for i in range(3)]
    for lesson in made:
        lessons.verify_lesson(db_session, lesson.id, actor_id=admin.id)
    entries = lessons.verified_lesson_entries(db_session, record_types={"thermo"}, limit=2)
    assert [e.source_key for e in entries] == [f"lesson:{made[2].id}", f"lesson:{made[1].id}"]


def test_scope_keys_reach_the_entry(db_session, people):
    curator, admin = people
    lesson = _propose(db_session, curator, scope=LessonScope(software="orca", check_code=CODE))
    lessons.verify_lesson(db_session, lesson.id, actor_id=admin.id)
    (entry,) = lessons.verified_lesson_entries(db_session, record_types={"thermo"})
    assert entry.fields["software"] == "orca" and entry.codes == (CODE,)
    assert entry.fields["category"] == "thermo"


def test_list_is_newest_first_and_filters_by_status(db_session, people):
    curator, admin = people
    first, second = _propose(db_session, curator, text="one"), _propose(db_session, curator, text="two")
    lessons.verify_lesson(db_session, first.id, actor_id=admin.id)
    rows, total = lessons.list_lessons(db_session)
    assert (total, [r.id for r in rows]) == (2, [second.id, first.id])
    rows, total = lessons.list_lessons(db_session, status=S.verified)
    assert (total, [r.id for r in rows]) == (1, [first.id])


def test_a_lesson_from_a_task_whose_finding_cannot_be_recovered_has_no_category(db_session, people):
    from app.db.models.common import MachineReviewCuratorTaskState as TaskState
    from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
    from tests.services._machine_review_job_support import make_submission

    _, admin = people
    submission = make_submission(db_session, admin.id)
    task = MachineReviewCuratorTask(
        submission_id=submission.id,
        record_type=SubmissionRecordType.kinetics,
        record_id=7,
        finding_fingerprint="b" * 64,
        workflow_state=TaskState.needs_curator_review,
        machine_review_status="machine_screened_warning",
        highest_severity="warning",
        findings_count=1,
    )
    db_session.add(task)
    db_session.flush()

    lesson = lessons.propose_lesson_from_task(db_session, task, "The finding was wrong.", admin.id)
    assert lesson.category is None
    assert lesson.record_type is SubmissionRecordType.kinetics
    assert lesson.signal is MachineReviewLessonSignal.curator_dismissed_finding
    assert lesson.source_curator_task_id == task.id and lesson.status is S.proposed


def test_an_unknown_check_code_is_refused_on_propose_and_edit_and_a_known_one_ranks_first(db_session, people):
    from app.api.errors import MachineReviewJobError
    from app.services.machine_review.agent.guidance import GuidanceIndex

    curator, admin = people
    with pytest.raises(MachineReviewJobError) as refused:
        _propose(db_session, curator, scope=LessonScope(check_code="no_such_check_code"))
    assert (refused.value.status, refused.value.code) == (422, "machine_review_lesson_unknown_check_code")

    plain = _propose(db_session, curator, text="atom map unaccounted note")
    with pytest.raises(MachineReviewJobError) as edit_refused:
        lessons.edit_lesson(db_session, plain.id, actor_id=admin.id, scope=LessonScope(check_code="nope"))
    assert edit_refused.value.code == "machine_review_lesson_unknown_check_code"

    scoped = _propose(db_session, curator, text="atom map unaccounted note", scope=LessonScope(check_code=CODE))
    for lesson in (plain, scoped):
        lessons.verify_lesson(db_session, lesson.id, actor_id=admin.id)
    entries = lessons.verified_lesson_entries(db_session, record_types={"thermo"})
    ranked = GuidanceIndex(entries).search(CODE, limit=5)
    assert ranked[0].source_key == f"lesson:{scoped.id}"
