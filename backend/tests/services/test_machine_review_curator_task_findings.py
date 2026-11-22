"""Recovering the finding behind a curator task.

A task stores only a fingerprint of its finding. The finding is recovered by
re-projecting the submission's stored machine-review events and matching that
fingerprint exactly, limited to the source event when the task records one.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from app.db.models.common import (
    MachineReviewSeverity as DBSeverity,
)
from app.db.models.common import (
    MachineReviewStatus as DBStatus,
)
from app.db.models.common import (
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.services.machine_review import (
    build_curator_tasks_for_submission,
    build_submission_machine_review_inspection,
)
from app.services.machine_review.curator_task_findings import (
    curator_task_ids_by_fingerprint,
    resolve_curator_task_findings,
)
from app.services.machine_review.curator_tasks import compute_finding_fingerprint
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_FORMAT,
    MachineReviewCategory,
    MachineReviewProviderFinding,
    MachineReviewProviderResult,
    MachineReviewSeverity,
    MachineReviewStatus,
)
from app.db.models.common import CuratorTaskFindingMatch
from app.services.submission import (
    create_submission,
    link_record,
    record_machine_review_audit_event,
)
from tests.services.scientific_read._factories import make_species


def _finding(species_id: int, message: str) -> MachineReviewProviderFinding:
    return MachineReviewProviderFinding(
        severity=MachineReviewSeverity.warning,
        category=MachineReviewCategory.provenance,
        record_type="species",
        record_id=species_id,
        message=message,
        evidence_keys=("k",),
    )


def _record(db_session, submission, *findings):
    ev = record_machine_review_audit_event(
        db_session,
        submission=submission,
        result=MachineReviewProviderResult(
            format=MACHINE_REVIEW_FORMAT,
            status=MachineReviewStatus.machine_screened_warning,
            summary="s",
            findings=findings,
            model="m",
            used_rag=False,
        ),
        provider="p",
    )
    db_session.flush()
    return ev


def _submission(db_session, user_id, species):
    submission = create_submission(
        db_session, created_by=user_id, submission_kind=SubmissionKind.thermo, title="t", summary="s"
    )
    db_session.flush()
    link_record(
        db_session, submission=submission, record_type=SubmissionRecordType.species,
        record_id=species.id, role="primary",
    )
    db_session.flush()
    db_session.refresh(submission)
    return submission


def _build(db_session, submission):
    db_session.refresh(submission)
    inspection = build_submission_machine_review_inspection(
        submission_id=submission.id,
        submission_record_links=submission.record_links,
        submission_audit_events=submission.audit_events,
    )
    return build_curator_tasks_for_submission(db_session, inspection=inspection)


def _tasks(db_session, submission_id):
    return list(
        db_session.query(MachineReviewCuratorTask)
        .filter_by(submission_id=submission_id)
        .order_by(MachineReviewCuratorTask.id)
    )


@pytest.fixture
def user_id(_api_admin_user):
    return _api_admin_user


def test_source_event_match(db_session, user_id):
    species = make_species(db_session)
    submission = _submission(db_session, user_id, species)
    ev = _record(db_session, submission, _finding(species.id, "A"), _finding(species.id, "B"))
    _build(db_session, submission)
    tasks = _tasks(db_session, submission.id)

    resolved = resolve_curator_task_findings(db_session, tasks)

    assert {resolved[t.id].finding.message for t in tasks} == {"A", "B"}
    for t in tasks:
        assert resolved[t.id].match is CuratorTaskFindingMatch.source_audit_event
        assert resolved[t.id].audit_event_ids == (ev.id,)


def test_fallback_match_after_rerun(db_session, user_id):
    species = make_species(db_session)
    submission = _submission(db_session, user_id, species)
    first = _record(db_session, submission, _finding(species.id, "A"))
    _build(db_session, submission)
    second = _record(db_session, submission, _finding(species.id, "A"), _finding(species.id, "B"))
    _build(db_session, submission)
    tasks = _tasks(db_session, submission.id)
    assert all(t.source_audit_event_id is None for t in tasks)

    resolved = resolve_curator_task_findings(db_session, tasks)

    by_message = {resolved[t.id].finding.message: resolved[t.id] for t in tasks}
    assert by_message["A"].match is CuratorTaskFindingMatch.submission_audit_events
    assert by_message["A"].audit_event_ids == (first.id, second.id)
    assert by_message["B"].audit_event_ids == (second.id,)


def test_source_event_limits_the_match(db_session, user_id):
    """A task pointing at event 1 does not claim a finding that only event 2 holds."""
    species = make_species(db_session)
    submission = _submission(db_session, user_id, species)
    first = _record(db_session, submission, _finding(species.id, "A"))
    second = _record(db_session, submission, _finding(species.id, "B"))
    fp_b = compute_finding_fingerprint(
        finding=_as_service_finding(_finding(species.id, "B")),
        record_type="species", record_id=species.id,
    )
    task = MachineReviewCuratorTask(
        submission_id=submission.id, record_type=SubmissionRecordType.species,
        record_id=species.id, finding_fingerprint=fp_b,
        machine_review_status=DBStatus.machine_screened_warning,
        highest_severity=DBSeverity.warning, source_audit_event_id=first.id,
    )
    db_session.add(task)
    db_session.flush()

    resolved = resolve_curator_task_findings(db_session, [task])[task.id]

    # B exists, but not in the event the task names: the fallback still finds it, and says so.
    assert resolved.finding.message == "B"
    assert resolved.match is CuratorTaskFindingMatch.submission_audit_events
    assert resolved.audit_event_ids == (second.id,)


def _as_service_finding(f: MachineReviewProviderFinding):
    from app.services.machine_review.schemas import MachineReviewFinding

    return MachineReviewFinding(
        severity=f.severity, category=f.category, record_type=f.record_type,
        record_ref=str(f.record_id), message=f.message, evidence_keys=f.evidence_keys,
        recommended_action=f.recommended_action,
    )


def test_not_found_for_unmatched_fingerprint(db_session, user_id):
    species = make_species(db_session)
    submission = _submission(db_session, user_id, species)
    _record(db_session, submission, _finding(species.id, "A"))
    f = _as_service_finding(_finding(species.id, "A"))
    with_context = compute_finding_fingerprint(
        finding=f, record_type="species", record_id=species.id, review_context="ctx"
    )
    task = MachineReviewCuratorTask(
        submission_id=submission.id, record_type=SubmissionRecordType.species,
        record_id=species.id, finding_fingerprint=with_context,
        machine_review_status=DBStatus.machine_screened_warning,
        highest_severity=DBSeverity.warning,
    )
    db_session.add(task)
    db_session.flush()

    resolved = resolve_curator_task_findings(db_session, [task])[task.id]

    assert resolved.finding is None
    assert resolved.match is CuratorTaskFindingMatch.not_found
    assert resolved.audit_event_ids == ()


def test_task_ids_by_fingerprint(db_session, user_id):
    species = make_species(db_session)
    submission = _submission(db_session, user_id, species)
    _record(db_session, submission, _finding(species.id, "A"))
    _build(db_session, submission)
    (task,) = _tasks(db_session, submission.id)

    mapping = curator_task_ids_by_fingerprint(db_session, submission.id)

    assert mapping == {("species", species.id, task.finding_fingerprint): task.id}


def test_query_count_is_constant_across_tasks_and_submissions(db_session, user_id):
    def _seed(n):
        for i in range(n):
            species = make_species(db_session)
            submission = _submission(db_session, user_id, species)
            _record(db_session, submission, _finding(species.id, f"A{i}"), _finding(species.id, f"B{i}"))
            _build(db_session, submission)

    def _count():
        tasks = list(db_session.query(MachineReviewCuratorTask).order_by(MachineReviewCuratorTask.id))
        statements = 0
        engine = db_session.connection().engine

        def _before(*args):
            nonlocal statements
            statements += 1

        event.listen(engine, "before_cursor_execute", _before)
        try:
            resolved = resolve_curator_task_findings(db_session, tasks)
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        assert all(r.finding is not None for r in resolved.values())
        return statements, len(tasks)

    _seed(2)
    two_q, two_n = _count()
    _seed(4)
    six_q, six_n = _count()
    assert (two_n, six_n) == (4, 12)
    assert six_q == two_q
