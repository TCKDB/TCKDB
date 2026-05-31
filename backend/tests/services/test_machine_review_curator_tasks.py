"""Tests for the machine-review curator-task creation service.

Exercises ``app/services/machine_review/curator_tasks.py``: building/upserting
:class:`MachineReviewCuratorTask` rows from a machine-review inspection
projection. These prove the creation policy (spec §6), the dedup/upsert
identity (spec §4), the snapshot/refresh behaviour (spec §3/§6), the
terminal-task guard (spec §5/§7), and the non-interference boundary (spec §9):
the service writes only curator-task rows and never touches submission status,
record review, or scientific state.

The inspection projection is constructed directly from the real read-model
dataclasses (``RecordMachineReview`` / ``MachineReviewRecordSummary`` /
``SubmissionMachineReviewInspection``) so the service is tested against the
exact shapes the inspection layer produces, without a full DB-backed
projection run.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db.models.common import (
    MachineReviewCuratorTaskState,
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.record_review import RecordReview
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review import (
    build_curator_tasks_for_submission,
    compute_finding_fingerprint,
)
from app.services.machine_review.derivation import (
    MachineReviewOutcome,
    derive_machine_review_status,
)
from app.services.machine_review.inspection import (
    SubmissionMachineReviewInspection,
    SubmissionRecordMachineReviewInspection,
)
from app.services.machine_review.mapping import UnmappedFinding, UnmappedReason
from app.services.machine_review.read_model import (
    RecordMachineReview,
    build_machine_review_record_summary,
)
from app.services.machine_review.schemas import (
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewSeverity,
)

_T0 = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Builders
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


def _finding(
    severity: MachineReviewSeverity,
    *,
    record_type: str = "kinetics",
    record_ref: str = "kin_abc",
    message: str = "note mentions tunneling but tunneling_model is null",
    category: MachineReviewCategory = MachineReviewCategory.kinetics,
    evidence_keys: tuple[str, ...] = ("tunneling_model",),
    recommended_action: str | None = "check tunneling_model",
) -> MachineReviewFinding:
    return MachineReviewFinding(
        severity=severity,
        category=category,
        record_type=record_type,
        record_ref=record_ref,
        message=message,
        evidence_keys=evidence_keys,
        recommended_action=recommended_action,
    )


def _record_inspection(
    *,
    findings: tuple[MachineReviewFinding, ...],
    record_type: str = "kinetics",
    record_ref: str = "kin_abc",
    record_id: int = 101,
    reviewed_at: datetime | None = _T0,
    model: str | None = "gpt-x",
    provider: str | None = "openai",
    submission_id: int | None = None,
) -> SubmissionRecordMachineReviewInspection:
    review = RecordMachineReview(
        record_type=record_type,
        record_ref=record_ref,
        status=derive_machine_review_status(findings, MachineReviewOutcome.completed),
        findings=findings,
        model=model,
        provider=provider,
        reviewed_at=reviewed_at,
        submission_id=submission_id,
        record_id=record_id,
    )
    summary = build_machine_review_record_summary(
        record_type=record_type, record_ref=record_ref, reviews=[review]
    )
    return SubmissionRecordMachineReviewInspection(
        record_type=record_type,
        record_ref=record_ref,
        record_id=record_id,
        latest_summary=summary,
        all_record_reviews=(review,),
    )


def _inspection(
    submission_id: int,
    *,
    record_inspections: tuple[SubmissionRecordMachineReviewInspection, ...] = (),
    unmapped_findings: tuple[UnmappedFinding, ...] = (),
    parse_warnings: tuple[str, ...] = (),
    source_audit_event_ids: tuple[int, ...] = (),
) -> SubmissionMachineReviewInspection:
    return SubmissionMachineReviewInspection(
        submission_id=submission_id,
        record_inspections=record_inspections,
        unmapped_findings=unmapped_findings,
        parse_warnings=parse_warnings,
        source_audit_event_ids=source_audit_event_ids,
    )


def _count_tasks(db_session, submission_id: int) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(MachineReviewCuratorTask)
        .where(MachineReviewCuratorTask.submission_id == submission_id)
    )


# --------------------------------------------------------------------------- #
# Creation policy by severity
# --------------------------------------------------------------------------- #


def test_creates_task_for_warning_record_finding(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    inspection = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(findings=(_finding(MachineReviewSeverity.warning),)),
        ),
    )
    result = build_curator_tasks_for_submission(db_session, inspection=inspection)

    assert result.created_count == 1
    assert len(result.task_ids) == 1
    task = db_session.get(MachineReviewCuratorTask, result.task_ids[0])
    assert task.workflow_state is MachineReviewCuratorTaskState.needs_curator_review
    assert task.highest_severity.value == "warning"


def test_creates_task_for_critical_record_finding(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    inspection = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(findings=(_finding(MachineReviewSeverity.critical),)),
        ),
    )
    result = build_curator_tasks_for_submission(db_session, inspection=inspection)

    assert result.created_count == 1
    task = db_session.get(MachineReviewCuratorTask, result.task_ids[0])
    assert task.workflow_state is MachineReviewCuratorTaskState.needs_curator_review
    assert task.highest_severity.value == "critical"


def test_does_not_create_task_for_info_finding(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    inspection = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(findings=(_finding(MachineReviewSeverity.info),)),
        ),
    )
    result = build_curator_tasks_for_submission(db_session, inspection=inspection)

    assert result.created_count == 0
    assert result.skipped_info_count == 1
    assert _count_tasks(db_session, submission.id) == 0


def test_does_not_create_task_for_submission_scoped_finding(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    # A submission-scoped finding has no record_type -> the projection routes it
    # to unmapped diagnostics, never to a record inspection.
    scoped = UnmappedFinding(
        finding=_finding(MachineReviewSeverity.critical, record_type=None),
        reason=UnmappedReason.submission_scoped,
    )
    inspection = _inspection(submission.id, unmapped_findings=(scoped,))
    result = build_curator_tasks_for_submission(db_session, inspection=inspection)

    assert result.created_count == 0
    assert result.skipped_unmapped_count == 1
    assert _count_tasks(db_session, submission.id) == 0


def test_does_not_create_task_for_unmapped_finding(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    unmapped = UnmappedFinding(
        finding=_finding(MachineReviewSeverity.warning, record_ref="not_linked"),
        reason=UnmappedReason.unlinked_record,
    )
    inspection = _inspection(submission.id, unmapped_findings=(unmapped,))
    result = build_curator_tasks_for_submission(db_session, inspection=inspection)

    assert result.created_count == 0
    assert result.skipped_unmapped_count == 1
    assert _count_tasks(db_session, submission.id) == 0


def test_does_not_create_task_for_parse_warning(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    inspection = _inspection(
        submission.id,
        parse_warnings=("provider output was not valid JSON",),
    )
    result = build_curator_tasks_for_submission(db_session, inspection=inspection)

    assert result.created_count == 0
    assert _count_tasks(db_session, submission.id) == 0


# --------------------------------------------------------------------------- #
# Upsert / dedup
# --------------------------------------------------------------------------- #


def test_upsert_reuses_existing_task(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    inspection = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(findings=(_finding(MachineReviewSeverity.warning),)),
        ),
    )
    first = build_curator_tasks_for_submission(db_session, inspection=inspection)
    second = build_curator_tasks_for_submission(db_session, inspection=inspection)

    assert first.created_count == 1
    assert second.created_count == 0
    assert second.reused_count == 1
    assert first.task_ids == second.task_ids
    assert _count_tasks(db_session, submission.id) == 1


def test_upsert_does_not_duplicate_on_rerun_with_new_audit_event(
    db_session, _api_test_user
):
    submission = _make_submission(db_session, _api_test_user)
    event_1 = _make_audit_event(db_session, submission.id)
    event_2 = _make_audit_event(db_session, submission.id)
    finding = _finding(MachineReviewSeverity.warning)

    insp_1 = _inspection(
        submission.id,
        record_inspections=(_record_inspection(findings=(finding,)),),
        source_audit_event_ids=(event_1.id,),
    )
    first = build_curator_tasks_for_submission(db_session, inspection=insp_1)

    # A fresh precheck run produces a new audit event id; identity is unchanged.
    insp_2 = _inspection(
        submission.id,
        record_inspections=(_record_inspection(findings=(finding,)),),
        source_audit_event_ids=(event_2.id,),
    )
    second = build_curator_tasks_for_submission(db_session, inspection=insp_2)

    assert first.created_count == 1
    assert second.created_count == 0
    assert second.reused_count == 1
    assert _count_tasks(db_session, submission.id) == 1
    # Snapshot provenance refreshed to the newer event; identity row preserved.
    task = db_session.get(MachineReviewCuratorTask, first.task_ids[0])
    assert task.source_audit_event_id == event_2.id


# --------------------------------------------------------------------------- #
# Fingerprint
# --------------------------------------------------------------------------- #


def test_fingerprint_excludes_source_audit_event_id():
    """The fingerprint function has no audit-event input and is deterministic,
    so re-runs producing new audit events cannot change it."""
    finding = _finding(MachineReviewSeverity.warning)
    fp1 = compute_finding_fingerprint(
        finding=finding, record_type="kinetics", record_id=101
    )
    fp2 = compute_finding_fingerprint(
        finding=finding, record_type="kinetics", record_id=101
    )
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex, matches the String(64) column


def test_fingerprint_excludes_model_provider(db_session, _api_test_user):
    """Two reviews of the same concern by different models/providers collapse to
    one task (model/provider are not identity-bearing)."""
    submission = _make_submission(db_session, _api_test_user)
    finding = _finding(MachineReviewSeverity.warning)

    insp_a = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(findings=(finding,), model="gpt-x", provider="openai"),
        ),
    )
    insp_b = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(
                findings=(finding,), model="claude-y", provider="anthropic"
            ),
        ),
    )
    first = build_curator_tasks_for_submission(db_session, inspection=insp_a)
    second = build_curator_tasks_for_submission(db_session, inspection=insp_b)

    assert first.created_count == 1
    assert second.created_count == 0
    assert second.reused_count == 1
    assert _count_tasks(db_session, submission.id) == 1


# --------------------------------------------------------------------------- #
# Existing-task behaviour
# --------------------------------------------------------------------------- #


def test_terminal_task_is_not_reopened_by_default(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    inspection = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(findings=(_finding(MachineReviewSeverity.warning),)),
        ),
    )
    first = build_curator_tasks_for_submission(db_session, inspection=inspection)
    task = db_session.get(MachineReviewCuratorTask, first.task_ids[0])

    # Resolve it terminally (full resolution triple, per the CHECK).
    task.workflow_state = MachineReviewCuratorTaskState.dismissed_machine_finding
    task.resolved_at = datetime(2026, 5, 31, 9, 0, 0)
    task.resolved_by = _api_test_user
    task.resolution_note = "False positive; descriptive note only."
    db_session.flush()

    second = build_curator_tasks_for_submission(db_session, inspection=inspection)

    assert second.created_count == 0
    assert second.skipped_terminal_count == 1
    assert _count_tasks(db_session, submission.id) == 1
    db_session.refresh(task)
    assert task.workflow_state is MachineReviewCuratorTaskState.dismissed_machine_finding
    assert task.resolution_note == "False positive; descriptive note only."


def test_open_task_snapshot_can_refresh_without_losing_assignment(
    db_session, _api_test_user
):
    submission = _make_submission(db_session, _api_test_user)
    inspection = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(findings=(_finding(MachineReviewSeverity.warning),)),
        ),
    )
    first = build_curator_tasks_for_submission(db_session, inspection=inspection)
    task = db_session.get(MachineReviewCuratorTask, first.task_ids[0])

    # A curator picks the task up and is assigned to it.
    task.assigned_to = _api_test_user
    task.workflow_state = MachineReviewCuratorTaskState.in_curator_review
    db_session.flush()

    # Re-run with a richer record-level snapshot (two findings -> findings_count
    # 2). Refresh must update the snapshot without disturbing assignment/state.
    richer = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(
                findings=(
                    _finding(MachineReviewSeverity.warning),
                    _finding(
                        MachineReviewSeverity.critical,
                        message="second concern",
                        evidence_keys=("other_key",),
                    ),
                )
            ),
        ),
    )
    second = build_curator_tasks_for_submission(
        db_session, inspection=richer, refresh_open_snapshots=True
    )

    db_session.refresh(task)
    assert second.reused_count >= 1
    assert second.refreshed_count >= 1
    assert task.assigned_to == _api_test_user
    assert task.workflow_state is MachineReviewCuratorTaskState.in_curator_review
    assert task.findings_count == 2  # snapshot refreshed
    assert task.highest_severity.value == "critical"


# --------------------------------------------------------------------------- #
# Non-interference
# --------------------------------------------------------------------------- #


def test_task_creation_does_not_mutate_submission_status(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    status_before = submission.status
    inspection = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(findings=(_finding(MachineReviewSeverity.warning),)),
        ),
    )
    build_curator_tasks_for_submission(db_session, inspection=inspection)
    db_session.refresh(submission)
    assert submission.status is status_before


def test_task_creation_does_not_mutate_scientific_records(db_session, _api_test_user):
    """The only rows the service writes are curator-task rows."""
    submission = _make_submission(db_session, _api_test_user)
    record_review_count_before = db_session.scalar(
        select(func.count()).select_from(RecordReview)
    )
    inspection = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(findings=(_finding(MachineReviewSeverity.critical),)),
        ),
    )
    build_curator_tasks_for_submission(db_session, inspection=inspection)

    record_review_count_after = db_session.scalar(
        select(func.count()).select_from(RecordReview)
    )
    assert record_review_count_after == record_review_count_before
    assert _count_tasks(db_session, submission.id) == 1


def test_task_creation_does_not_touch_record_review_status(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    inspection = _inspection(
        submission.id,
        record_inspections=(
            _record_inspection(
                findings=(_finding(MachineReviewSeverity.warning),), record_id=101
            ),
        ),
    )
    build_curator_tasks_for_submission(db_session, inspection=inspection)

    # No record_review row was created for the addressed record.
    review = db_session.scalar(
        select(RecordReview).where(
            RecordReview.record_type == SubmissionRecordType.kinetics,
            RecordReview.record_id == 101,
        )
    )
    assert review is None


# --------------------------------------------------------------------------- #
# Result counts
# --------------------------------------------------------------------------- #


def test_task_creation_result_counts_are_correct(db_session, _api_test_user):
    submission = _make_submission(db_session, _api_test_user)
    # One record with critical + warning + info findings, plus a stray unmapped
    # finding at the submission level.
    record = _record_inspection(
        findings=(
            _finding(MachineReviewSeverity.critical, message="c", evidence_keys=("a",)),
            _finding(MachineReviewSeverity.warning, message="w", evidence_keys=("b",)),
            _finding(MachineReviewSeverity.info, message="i", evidence_keys=("c",)),
        )
    )
    unmapped = UnmappedFinding(
        finding=_finding(MachineReviewSeverity.warning, record_type=None),
        reason=UnmappedReason.submission_scoped,
    )
    inspection = _inspection(
        submission.id,
        record_inspections=(record,),
        unmapped_findings=(unmapped,),
    )
    result = build_curator_tasks_for_submission(db_session, inspection=inspection)

    assert result.created_count == 2  # critical + warning
    assert result.skipped_info_count == 1
    assert result.skipped_unmapped_count == 1
    assert result.reused_count == 0
    assert result.skipped_terminal_count == 0
    assert len(result.task_ids) == 2
    assert _count_tasks(db_session, submission.id) == 2
