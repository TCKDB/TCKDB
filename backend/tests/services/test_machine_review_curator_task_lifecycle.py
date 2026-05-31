"""Tests for the machine-review curator-task lifecycle service.

Exercises ``app/services/machine_review/curator_task_lifecycle.py``:
assignment, start-review, resolution, and reopen transitions over an existing
:class:`MachineReviewCuratorTask`. These prove the state rules (open vs
terminal), atomic resolution, terminal idempotency/rejection, reopen field
clearing, and the non-interference boundary — lifecycle ops mutate only the
task's workflow columns, never submission status, record review, or scientific
state.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.api.errors import DomainError, NotFoundError
from app.db.models.common import (
    MachineReviewCuratorTaskState,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.common import MachineReviewSeverity as DBMachineReviewSeverity
from app.db.models.common import MachineReviewStatus as DBMachineReviewStatus
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.record_review import RecordReview
from app.db.models.submission import Submission
from app.services.machine_review import (
    assign_curator_task,
    reopen_curator_task,
    resolve_curator_task,
    start_curator_task_review,
)

_STATE = MachineReviewCuratorTaskState


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _make_submission(db_session, created_by: int) -> Submission:
    submission = Submission(created_by=created_by, submission_kind=SubmissionKind.kinetics)
    db_session.add(submission)
    db_session.flush()
    return submission


def _make_task(
    db_session,
    submission_id: int,
    *,
    workflow_state: MachineReviewCuratorTaskState = _STATE.needs_curator_review,
    assigned_to: int | None = None,
    resolved_by: int | None = None,
    resolution_note: str | None = None,
    fingerprint: str = "a" * 64,
) -> MachineReviewCuratorTask:
    """Insert one task. Terminal states auto-fill the resolution triple so the
    seeded row satisfies the DB resolution-consistency CHECK."""
    is_terminal = workflow_state in _STATE.terminal_states()
    resolved_at = datetime(2026, 5, 31, 9, 0, 0) if is_terminal else None
    if is_terminal:
        resolution_note = resolution_note or "seed terminal note"
        # resolved_by must be supplied by the caller for a terminal seed.
        assert resolved_by is not None, "terminal seed needs resolved_by"

    task = MachineReviewCuratorTask(
        submission_id=submission_id,
        record_type=SubmissionRecordType.kinetics,
        record_id=101,
        finding_fingerprint=fingerprint,
        workflow_state=workflow_state,
        machine_review_status=DBMachineReviewStatus.machine_screened_warning,
        highest_severity=DBMachineReviewSeverity.warning,
        assigned_to=assigned_to,
        resolved_by=resolved_by,
        resolved_at=resolved_at,
        resolution_note=resolution_note,
    )
    db_session.add(task)
    db_session.flush()
    return task


# --------------------------------------------------------------------------- #
# Assignment
# --------------------------------------------------------------------------- #


def test_assign_task_sets_assigned_to(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id)
    assign_curator_task(db_session, task_id=task.id, assignee_id=_api_test_user)
    db_session.refresh(task)
    assert task.assigned_to == _api_test_user


def test_assign_task_does_not_change_workflow_state(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id, workflow_state=_STATE.needs_curator_review)
    assign_curator_task(db_session, task_id=task.id, assignee_id=_api_test_user)
    db_session.refresh(task)
    assert task.workflow_state is _STATE.needs_curator_review


def test_unassign_task_clears_assigned_to(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id, assigned_to=_api_test_user)
    assign_curator_task(db_session, task_id=task.id, assignee_id=None)
    db_session.refresh(task)
    assert task.assigned_to is None


def test_assign_terminal_task_rejected_by_default(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(
        db_session,
        submission.id,
        workflow_state=_STATE.resolved_no_action,
        resolved_by=_api_test_user,
    )
    with pytest.raises(DomainError):
        assign_curator_task(db_session, task_id=task.id, assignee_id=_api_test_user)

    # Explicit override is allowed and does not change the terminal state.
    assign_curator_task(
        db_session, task_id=task.id, assignee_id=_api_test_user, allow_terminal=True
    )
    db_session.refresh(task)
    assert task.assigned_to == _api_test_user
    assert task.workflow_state is _STATE.resolved_no_action


# --------------------------------------------------------------------------- #
# Start review
# --------------------------------------------------------------------------- #


def test_start_review_from_untriaged(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id, workflow_state=_STATE.untriaged)
    start_curator_task_review(db_session, task_id=task.id)
    db_session.refresh(task)
    assert task.workflow_state is _STATE.in_curator_review


def test_start_review_from_needs_curator_review(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id, workflow_state=_STATE.needs_curator_review)
    start_curator_task_review(db_session, task_id=task.id)
    db_session.refresh(task)
    assert task.workflow_state is _STATE.in_curator_review


def test_start_review_idempotent_when_already_in_review(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(
        db_session,
        submission.id,
        workflow_state=_STATE.in_curator_review,
        assigned_to=_api_test_user,
    )
    start_curator_task_review(db_session, task_id=task.id)
    db_session.refresh(task)
    assert task.workflow_state is _STATE.in_curator_review
    assert task.assigned_to == _api_test_user  # untouched


def test_start_review_rejects_terminal_task(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(
        db_session,
        submission.id,
        workflow_state=_STATE.dismissed_machine_finding,
        resolved_by=_api_test_user,
    )
    with pytest.raises(DomainError):
        start_curator_task_review(db_session, task_id=task.id)


def test_start_review_assigns_actor_if_unassigned(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id, workflow_state=_STATE.untriaged)
    start_curator_task_review(db_session, task_id=task.id, actor_id=_api_test_user)
    db_session.refresh(task)
    assert task.workflow_state is _STATE.in_curator_review
    assert task.assigned_to == _api_test_user


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def test_resolve_no_action_requires_note(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id)
    for bad_note in ("", "   "):
        with pytest.raises(DomainError):
            resolve_curator_task(
                db_session,
                task_id=task.id,
                resolution=_STATE.resolved_no_action,
                resolved_by=_api_test_user,
                resolution_note=bad_note,
            )
    db_session.refresh(task)
    assert task.workflow_state is _STATE.needs_curator_review  # unchanged


def test_resolve_dismissed_machine_finding_requires_note(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id)
    with pytest.raises(DomainError):
        resolve_curator_task(
            db_session,
            task_id=task.id,
            resolution=_STATE.dismissed_machine_finding,
            resolved_by=_api_test_user,
            resolution_note="",
        )


def test_resolve_human_reviewed_does_not_change_record_review_status(
    db_session, _api_test_user
):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id)
    resolve_curator_task(
        db_session,
        task_id=task.id,
        resolution=_STATE.resolved_human_reviewed,
        resolved_by=_api_test_user,
        resolution_note="Approved via the human-review layer.",
    )
    db_session.refresh(task)
    assert task.workflow_state is _STATE.resolved_human_reviewed

    # No record_review row was written for the addressed record.
    review = db_session.scalar(
        select(RecordReview).where(
            RecordReview.record_type == SubmissionRecordType.kinetics,
            RecordReview.record_id == 101,
        )
    )
    assert review is None


def test_resolve_sets_resolved_fields_atomically(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id)
    resolve_curator_task(
        db_session,
        task_id=task.id,
        resolution=_STATE.resolved_no_action,
        resolved_by=_api_test_user,
        resolution_note="Looked; record is fine, nothing to change.",
    )
    db_session.refresh(task)
    assert task.workflow_state is _STATE.resolved_no_action
    assert task.resolved_by == _api_test_user
    assert task.resolved_at is not None
    assert task.resolution_note == "Looked; record is fine, nothing to change."


def test_resolve_terminal_same_resolution_is_idempotent(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id)
    resolve_curator_task(
        db_session,
        task_id=task.id,
        resolution=_STATE.dismissed_machine_finding,
        resolved_by=_api_test_user,
        resolution_note="False positive.",
    )
    db_session.refresh(task)
    first_resolved_at = task.resolved_at

    # Same terminal resolution again: idempotent, original record preserved.
    resolve_curator_task(
        db_session,
        task_id=task.id,
        resolution=_STATE.dismissed_machine_finding,
        resolved_by=_api_test_user,
        resolution_note="A different note that must not overwrite.",
    )
    db_session.refresh(task)
    assert task.workflow_state is _STATE.dismissed_machine_finding
    assert task.resolution_note == "False positive."  # not overwritten
    assert task.resolved_at == first_resolved_at


def test_resolve_terminal_different_resolution_rejected(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id)
    resolve_curator_task(
        db_session,
        task_id=task.id,
        resolution=_STATE.dismissed_machine_finding,
        resolved_by=_api_test_user,
        resolution_note="False positive.",
    )
    with pytest.raises(DomainError):
        resolve_curator_task(
            db_session,
            task_id=task.id,
            resolution=_STATE.resolved_no_action,
            resolved_by=_api_test_user,
            resolution_note="Changed my mind.",
        )


# --------------------------------------------------------------------------- #
# Reopen
# --------------------------------------------------------------------------- #


def test_reopen_terminal_task_clears_resolution_fields(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(
        db_session,
        submission.id,
        workflow_state=_STATE.resolved_human_reviewed,
        resolved_by=_api_test_user,
    )
    reopen_curator_task(db_session, task_id=task.id)
    db_session.refresh(task)
    assert task.workflow_state is _STATE.needs_curator_review
    assert task.resolved_at is None
    assert task.resolved_by is None
    assert task.resolution_note is None


def test_reopen_does_not_clear_assignment_by_default(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(
        db_session,
        submission.id,
        workflow_state=_STATE.dismissed_machine_finding,
        assigned_to=_api_test_user,
        resolved_by=_api_test_user,
    )
    reopen_curator_task(db_session, task_id=task.id)
    db_session.refresh(task)
    assert task.assigned_to == _api_test_user  # preserved by default

    # Re-resolve then reopen with explicit clear.
    resolve_curator_task(
        db_session,
        task_id=task.id,
        resolution=_STATE.dismissed_machine_finding,
        resolved_by=_api_test_user,
        resolution_note="again",
    )
    reopen_curator_task(db_session, task_id=task.id, clear_assignment=True)
    db_session.refresh(task)
    assert task.assigned_to is None


# --------------------------------------------------------------------------- #
# Non-interference
# --------------------------------------------------------------------------- #


def test_lifecycle_does_not_mutate_submission_status(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    status_before = submission.status
    task = _make_task(db_session, submission.id, workflow_state=_STATE.untriaged)

    assign_curator_task(db_session, task_id=task.id, assignee_id=_api_test_user)
    start_curator_task_review(db_session, task_id=task.id)
    resolve_curator_task(
        db_session,
        task_id=task.id,
        resolution=_STATE.resolved_no_action,
        resolved_by=_api_test_user,
        resolution_note="done",
    )
    db_session.refresh(submission)
    assert submission.status is status_before


def test_lifecycle_does_not_mutate_scientific_records(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    review_count_before = db_session.scalar(
        select(func.count()).select_from(RecordReview)
    )
    task = _make_task(db_session, submission.id, workflow_state=_STATE.untriaged)

    start_curator_task_review(db_session, task_id=task.id, actor_id=_api_test_user)
    resolve_curator_task(
        db_session,
        task_id=task.id,
        resolution=_STATE.resolved_human_reviewed,
        resolved_by=_api_test_user,
        resolution_note="reviewed elsewhere",
    )
    reopen_curator_task(db_session, task_id=task.id)

    review_count_after = db_session.scalar(
        select(func.count()).select_from(RecordReview)
    )
    assert review_count_after == review_count_before


# --------------------------------------------------------------------------- #
# Error surfaces
# --------------------------------------------------------------------------- #


def test_missing_task_raises_not_found(db_session, _api_test_user):
    with pytest.raises(NotFoundError):
        assign_curator_task(db_session, task_id=999_999_999, assignee_id=_api_test_user)


def test_resolve_rejects_non_terminal_target(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id)
    with pytest.raises(DomainError):
        resolve_curator_task(
            db_session,
            task_id=task.id,
            resolution=_STATE.in_curator_review,  # not a terminal state
            resolved_by=_api_test_user,
            resolution_note="invalid target",
        )
