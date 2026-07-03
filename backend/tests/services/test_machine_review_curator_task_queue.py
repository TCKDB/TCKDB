"""Model + migration tests for ``machine_review_curator_task``.

Covers the persisted curator task queue introduced by Alembic revision
``b8c9d0e1f2a3`` and the ORM model in
``app/db/models/machine_review_curator_task.py``. This is the *fourth* review
axis (human triage state over machine findings); see
``backend/docs/specs/machine_review_curator_task_queue.md``.

These tests assert the schema shape that the migration must build (table,
identity unique constraint, queue indexes, foreign-key targets) and the model
invariants the DB enforces (workflow_state default, nullable
assignment/resolution on creation, the resolution-consistency CHECK in both
directions). The enum drift-guard pins the DB-layer mirror enums to the
authoritative service-layer vocabulary.

Downgrade is verified out-of-band via the Alembic CLI (``downgrade -1`` /
``upgrade head``); the repo has no in-suite downgrade harness and running DDL
downgrade against the shared session DB would break sibling tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.db.models.common import (
    MachineReviewCuratorTaskState,
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.common import MachineReviewSeverity as DBMachineReviewSeverity
from app.db.models.common import MachineReviewStatus as DBMachineReviewStatus
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review import MachineReviewSeverity, MachineReviewStatus

_TABLE = "machine_review_curator_task"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_submission(db_session, created_by: int) -> Submission:
    submission = Submission(
        created_by=created_by,
        submission_kind=SubmissionKind.kinetics,
    )
    db_session.add(submission)
    db_session.flush()
    return submission


def _make_audit_event(db_session, submission_id: int) -> SubmissionAuditEvent:
    event = SubmissionAuditEvent(
        submission_id=submission_id,
        actor_kind=SubmissionActorKind.llm,
        event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
    )
    db_session.add(event)
    db_session.flush()
    return event


def _make_task(db_session, submission_id: int, **overrides) -> MachineReviewCuratorTask:
    defaults = {
        "submission_id": submission_id,
        "record_type": SubmissionRecordType.kinetics,
        "record_id": 101,
        "finding_fingerprint": "a" * 64,
        "machine_review_status": DBMachineReviewStatus.machine_screened_warning,
        "highest_severity": DBMachineReviewSeverity.warning,
    }
    defaults.update(overrides)
    task = MachineReviewCuratorTask(**defaults)
    db_session.add(task)
    return task


# --------------------------------------------------------------------------- #
# Structural / migration shape
# --------------------------------------------------------------------------- #


def test_table_exists_after_migration(db_engine):
    insp = inspect(db_engine)
    assert _TABLE in insp.get_table_names()


def test_identity_unique_constraint_columns(db_engine):
    insp = inspect(db_engine)
    uqs = {uq["name"]: set(uq["column_names"]) for uq in insp.get_unique_constraints(_TABLE)}
    assert "uq_machine_review_curator_task_identity" in uqs
    assert uqs["uq_machine_review_curator_task_identity"] == {
        "submission_id",
        "record_type",
        "record_id",
        "finding_fingerprint",
    }


def test_record_type_record_id_index_exists(db_engine):
    insp = inspect(db_engine)
    indexes = {ix["name"]: list(ix["column_names"]) for ix in insp.get_indexes(_TABLE)}
    assert "ix_machine_review_curator_task_record" in indexes
    assert indexes["ix_machine_review_curator_task_record"] == ["record_type", "record_id"]


def test_queue_indexes_exist(db_engine):
    insp = inspect(db_engine)
    names = {ix["name"] for ix in insp.get_indexes(_TABLE)}
    expected = {
        "ix_machine_review_curator_task_workflow_state",
        "ix_machine_review_curator_task_state_severity",
        "ix_machine_review_curator_task_assigned_to",
        "ix_machine_review_curator_task_record",
        "ix_machine_review_curator_task_submission_id",
        "ix_machine_review_curator_task_source_audit_event_id",
    }
    assert expected <= names


def test_foreign_keys_point_to_expected_tables(db_engine):
    insp = inspect(db_engine)
    fks = insp.get_foreign_keys(_TABLE)
    by_local_col = {tuple(fk["constrained_columns"]): fk["referred_table"] for fk in fks}
    assert by_local_col[("submission_id",)] == "submission"
    assert by_local_col[("source_audit_event_id",)] == "submission_audit_event"
    assert by_local_col[("assigned_to",)] == "app_user"
    assert by_local_col[("resolved_by",)] == "app_user"


# --------------------------------------------------------------------------- #
# Enum drift-guard
# --------------------------------------------------------------------------- #


def test_db_enums_mirror_service_layer_vocabulary():
    """The DB-layer snapshot enums must stay in lock-step with the
    authoritative service-layer machine-review vocabulary."""
    assert {e.value for e in DBMachineReviewStatus} == {
        e.value for e in MachineReviewStatus
    }
    assert {e.value for e in DBMachineReviewSeverity} == {
        e.value for e in MachineReviewSeverity
    }


def test_terminal_states_partition():
    terminal = MachineReviewCuratorTaskState.terminal_states()
    assert terminal == {
        MachineReviewCuratorTaskState.resolved_no_action,
        MachineReviewCuratorTaskState.resolved_human_reviewed,
        MachineReviewCuratorTaskState.dismissed_machine_finding,
    }
    # Open states are the complement and must not be terminal.
    for state in MachineReviewCuratorTaskState:
        assert state.is_terminal == (state in terminal)


# --------------------------------------------------------------------------- #
# ORM behaviour / DB-enforced invariants
# --------------------------------------------------------------------------- #


def test_workflow_state_defaults_to_untriaged(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(db_session, submission.id)
    db_session.flush()
    db_session.refresh(task)
    assert task.workflow_state is MachineReviewCuratorTaskState.untriaged
    assert task.findings_count == 1
    assert task.created_at is not None
    assert task.updated_at is not None


def test_open_task_allows_null_assignment_and_resolution(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    event = _make_audit_event(db_session, submission.id)
    task = _make_task(
        db_session,
        submission.id,
        source_audit_event_id=event.id,
        workflow_state=MachineReviewCuratorTaskState.needs_curator_review,
    )
    db_session.flush()
    db_session.refresh(task)
    # Open state: assignment + all resolution fields legitimately NULL.
    assert task.assigned_to is None
    assert task.resolved_at is None
    assert task.resolved_by is None
    assert task.resolution_note is None
    assert task.source_audit_event_id == event.id


def test_duplicate_task_identity_rejected(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    _make_task(db_session, submission.id, finding_fingerprint="f" * 64)
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _make_task(db_session, submission.id, finding_fingerprint="f" * 64)
            db_session.flush()


def test_same_finding_different_record_allowed(db_session, _api_test_user):
    """Dedup is per (submission, record_type, record_id, fingerprint) — the
    same fingerprint on a *different* record is a distinct task."""
    submission = _make_submission(db_session, _api_test_user)
    _make_task(db_session, submission.id, record_id=101, finding_fingerprint="f" * 64)
    _make_task(db_session, submission.id, record_id=202, finding_fingerprint="f" * 64)
    db_session.flush()  # no IntegrityError


def test_terminal_state_requires_resolution_fields(db_session, _api_test_user):
    """A terminal workflow_state with missing resolution fields violates the
    resolution-consistency CHECK."""
    submission = _make_submission(db_session, _api_test_user)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _make_task(
                db_session,
                submission.id,
                workflow_state=MachineReviewCuratorTaskState.dismissed_machine_finding,
                # resolved_* deliberately left NULL
            )
            db_session.flush()


def test_open_state_forbids_resolution_fields(db_session, _api_test_user):
    """The CHECK is bidirectional: an open state with the full resolution
    triple set is also rejected (resolution belongs to terminal states only)."""
    from datetime import datetime

    submission = _make_submission(db_session, _api_test_user)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _make_task(
                db_session,
                submission.id,
                workflow_state=MachineReviewCuratorTaskState.in_curator_review,
                resolved_at=datetime(2026, 5, 31, 12, 0, 0),
                resolved_by=_api_test_user,
                resolution_note="should not be allowed while open",
            )
            db_session.flush()


def test_terminal_state_with_all_resolution_fields_ok(db_session, _api_test_user):
    from datetime import datetime

    submission = _make_submission(db_session, _api_test_user)
    task = _make_task(
        db_session,
        submission.id,
        workflow_state=MachineReviewCuratorTaskState.resolved_human_reviewed,
        resolved_at=datetime(2026, 5, 31, 12, 0, 0),
        resolved_by=_api_test_user,
        resolution_note="Reviewed via the human-review layer; record approved.",
    )
    db_session.flush()
    db_session.refresh(task)
    assert task.workflow_state is MachineReviewCuratorTaskState.resolved_human_reviewed
    assert task.resolution_note is not None


def test_findings_count_must_be_positive(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _make_task(db_session, submission.id, findings_count=0)
            db_session.flush()
