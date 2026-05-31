"""Curator-task creation service for machine-review findings.

This is the *write* counterpart to the read-only inspection projection
(:mod:`app.services.machine_review.inspection`). Given a submission's
machine-review inspection, it creates or reuses
:class:`~app.db.models.machine_review_curator_task.MachineReviewCuratorTask`
rows — the persisted human triage queue designed in
``backend/docs/specs/machine_review_curator_task_queue.md``.

Boundaries (spec §6/§9, and this slice's non-goals):

* **Explicit call only.** Nothing here runs on upload, precheck, or human
  review. A caller (a future admin endpoint or batch job) invokes
  :func:`build_curator_tasks_for_submission` against an already-computed
  inspection projection.
* **Non-interference.** The only rows written are
  ``machine_review_curator_task`` rows. This service never touches
  ``submission.status``, ``record_review`` / ``RecordReviewStatus``,
  scientific records, deterministic evidence, or any public ``trust.*``
  fragment.
* **Caller controls the transaction.** Like the sibling write services
  (e.g. :mod:`app.services.record_review`), this flushes but never commits.

What becomes a task (spec §6):

* Only **exact, mapped, record-level** findings (the ``record_inspections``
  of the projection). Submission-scoped, unmapped, and parse-warning
  diagnostics never become tasks — the inspection already routed them out of
  ``record_inspections`` and into the diagnostic buckets.
* Only ``warning`` and ``critical`` severities open a task
  (``needs_curator_review``). ``info`` findings create no task by default.
* One task per warning/critical finding on the record's latest review; the
  denormalised snapshot fields are the record-level summary, applied
  consistently across that record's tasks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import (
    MachineReviewCuratorTaskState,
    SubmissionRecordType,
)
from app.db.models.common import MachineReviewSeverity as DBMachineReviewSeverity
from app.db.models.common import MachineReviewStatus as DBMachineReviewStatus
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.services.machine_review.inspection import (
    SubmissionMachineReviewInspection,
    SubmissionRecordMachineReviewInspection,
)
from app.services.machine_review.read_model import (
    MachineReviewRecordSummary,
    RecordMachineReview,
    select_latest_machine_review_for_record,
)
from app.services.machine_review.schemas import (
    MachineReviewFinding,
    MachineReviewSeverity,
)

# Severities that open a curator task (spec §6 "Initial state by severity").
# ``info`` is intentionally absent — info findings create no task by default.
_TASK_OPENING_SEVERITIES: frozenset[MachineReviewSeverity] = frozenset(
    {MachineReviewSeverity.warning, MachineReviewSeverity.critical}
)


@dataclass(frozen=True)
class CuratorTaskBuildResult:
    """Outcome of one :func:`build_curator_tasks_for_submission` call.

    Counts are disjoint per finding considered: a warning/critical finding is
    counted in exactly one of created / reused / skipped_terminal.
    ``refreshed_count`` is a sub-count of ``reused_count`` (open tasks whose
    snapshot was refreshed in place) and so is *not* disjoint from it.
    ``skipped_info_count`` and ``skipped_unmapped_count`` track findings that
    deliberately never become tasks.
    """

    created_count: int = 0
    reused_count: int = 0
    refreshed_count: int = 0
    skipped_info_count: int = 0
    skipped_unmapped_count: int = 0
    skipped_terminal_count: int = 0
    task_ids: tuple[int, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


def compute_finding_fingerprint(
    *,
    finding: MachineReviewFinding,
    record_type: str,
    record_id: int,
    review_context: str | None = None,
) -> str:
    """Stable, derived identity hash for one finding on one record (spec §4).

    The fingerprint is a SHA-256 over a canonical, key-sorted JSON
    serialisation of the **identity-bearing** fields — the fields that make a
    finding "the same concern" rather than "a new concern". It is computed in
    the service layer and never accepted from a client (project convention).

    Deliberately excluded (spec §4):

    * ``source_audit_event_id`` — changes on every precheck re-run; including
      it would make each re-run look like a brand-new finding and defeat
      deduplication.
    * ``model`` / ``provider`` — identical concerns from different reviewers
      collapse to one task by default.
    * any timestamp (``created_at`` / ``reviewed_at``).

    ``review_context`` is an optional stable hash of the projection/finding
    context a caller may fold in; it must itself be free of the excluded
    fields. ``record_id`` is the resolved internal record id (the table is
    private; spec §3), pinning the finding to the exact record it concerns.
    """
    payload = {
        "severity": finding.severity.value,
        "category": finding.category.value,
        "record_type": record_type,
        "record_id": record_id,
        "message": finding.message,
        "evidence_keys": sorted(finding.evidence_keys),
        "recommended_action": finding.recommended_action,
        "review_context": review_context,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resolve_source_audit_event_id(
    inspection: SubmissionMachineReviewInspection,
    explicit: int | None,
) -> int | None:
    """Pick the provenance audit-event id for tasks built from this inspection.

    A caller may pass one explicitly. Otherwise we attach the submission's
    single machine-review event id when there is exactly one; with zero or
    several events the projection no longer points at a single event, so the
    (nullable, spec §3) provenance is left ``None`` rather than guessed.
    """
    if explicit is not None:
        return explicit
    if len(inspection.source_audit_event_ids) == 1:
        return inspection.source_audit_event_ids[0]
    return None


def build_curator_tasks_for_submission(
    session: Session,
    *,
    inspection: SubmissionMachineReviewInspection,
    source_audit_event_id: int | None = None,
    review_context: str | None = None,
    refresh_open_snapshots: bool = True,
    reopen_terminal: bool = False,
) -> CuratorTaskBuildResult:
    """Create or reuse curator tasks for one submission's mapped findings.

    Iterates only the projection's ``record_inspections`` (exact mapped
    record-level findings); submission-scoped, unmapped, and parse-warning
    diagnostics are counted as skipped and never become tasks. For each record
    it takes the latest review's warning/critical findings and upserts one task
    per finding, keyed by ``(submission_id, record_type, record_id,
    finding_fingerprint)``.

    Flushes but does not commit — the caller owns the transaction.
    """
    resolved_event_id = _resolve_source_audit_event_id(
        inspection, source_audit_event_id
    )

    created = 0
    reused = 0
    refreshed = 0
    skipped_info = 0
    skipped_terminal = 0
    task_ids: list[int] = []
    warnings: list[str] = []

    # Diagnostics never become tasks (spec §6). Counted for the caller's report.
    skipped_unmapped = len(inspection.unmapped_findings)

    for record_inspection in inspection.record_inspections:
        record_id = record_inspection.record_id
        if record_id is None:
            # The task table addresses records by internal id (NOT NULL). A
            # projection that could not carry one cannot produce a task.
            warnings.append(
                f"Record {record_inspection.record_type}/"
                f"{record_inspection.record_ref} has no resolved internal id; "
                "skipped (cannot key a task without record_id)."
            )
            continue

        try:
            record_type_enum = SubmissionRecordType(record_inspection.record_type)
        except ValueError:
            warnings.append(
                f"Record cites unknown record_type "
                f"{record_inspection.record_type!r}; skipped."
            )
            continue

        latest = _latest_review(record_inspection)
        if latest is None:
            continue

        summary = record_inspection.latest_summary

        for finding in latest.findings:
            if finding.severity not in _TASK_OPENING_SEVERITIES:
                skipped_info += 1
                continue

            fingerprint = compute_finding_fingerprint(
                finding=finding,
                record_type=record_inspection.record_type,
                record_id=record_id,
                review_context=review_context,
            )

            outcome = _upsert_task(
                session,
                submission_id=inspection.submission_id,
                record_type=record_type_enum,
                record_id=record_id,
                finding=finding,
                fingerprint=fingerprint,
                summary=summary,
                source_audit_event_id=resolved_event_id,
                refresh_open_snapshots=refresh_open_snapshots,
                reopen_terminal=reopen_terminal,
            )
            task_ids.append(outcome.task_id)
            if outcome.kind == "created":
                created += 1
            elif outcome.kind == "reused":
                reused += 1
                if outcome.refreshed:
                    refreshed += 1
            elif outcome.kind == "reopened":
                reused += 1
                refreshed += 1
            elif outcome.kind == "skipped_terminal":
                skipped_terminal += 1

    return CuratorTaskBuildResult(
        created_count=created,
        reused_count=reused,
        refreshed_count=refreshed,
        skipped_info_count=skipped_info,
        skipped_unmapped_count=skipped_unmapped,
        skipped_terminal_count=skipped_terminal,
        task_ids=tuple(task_ids),
        warnings=tuple(warnings),
    )


def _latest_review(
    record_inspection: SubmissionRecordMachineReviewInspection,
) -> RecordMachineReview | None:
    """The single latest review for this record (the one the summary reflects)."""
    return select_latest_machine_review_for_record(
        record_type=record_inspection.record_type,
        record_ref=record_inspection.record_ref or "",
        reviews=record_inspection.all_record_reviews,
    )


@dataclass(frozen=True)
class _UpsertOutcome:
    task_id: int
    kind: str  # "created" | "reused" | "reopened" | "skipped_terminal"
    refreshed: bool = False


def _upsert_task(
    session: Session,
    *,
    submission_id: int,
    record_type: SubmissionRecordType,
    record_id: int,
    finding: MachineReviewFinding,
    fingerprint: str,
    summary: MachineReviewRecordSummary,
    source_audit_event_id: int | None,
    refresh_open_snapshots: bool,
    reopen_terminal: bool,
) -> _UpsertOutcome:
    """Create the task, or reuse/refresh/skip an existing one for this identity."""
    existing = session.scalar(
        select(MachineReviewCuratorTask).where(
            MachineReviewCuratorTask.submission_id == submission_id,
            MachineReviewCuratorTask.record_type == record_type,
            MachineReviewCuratorTask.record_id == record_id,
            MachineReviewCuratorTask.finding_fingerprint == fingerprint,
        )
    )

    status_snapshot = DBMachineReviewStatus(summary.status.value)
    severity_snapshot = _severity_snapshot(summary, finding)
    findings_count = max(summary.findings_count, 1)

    if existing is None:
        task = MachineReviewCuratorTask(
            submission_id=submission_id,
            record_type=record_type,
            record_id=record_id,
            finding_fingerprint=fingerprint,
            workflow_state=MachineReviewCuratorTaskState.needs_curator_review,
            machine_review_status=status_snapshot,
            highest_severity=severity_snapshot,
            findings_count=findings_count,
            source_audit_event_id=source_audit_event_id,
        )
        session.add(task)
        session.flush()
        return _UpsertOutcome(task_id=task.id, kind="created")

    if existing.workflow_state.is_terminal:
        if not reopen_terminal:
            # Default: never reopen a resolved/dismissed task, never duplicate.
            return _UpsertOutcome(task_id=existing.id, kind="skipped_terminal")
        # Explicit reopen: clear the resolution triple (required by the
        # resolution-consistency CHECK) and refresh the snapshot.
        existing.workflow_state = MachineReviewCuratorTaskState.needs_curator_review
        existing.resolved_at = None
        existing.resolved_by = None
        existing.resolution_note = None
        existing.machine_review_status = status_snapshot
        existing.highest_severity = severity_snapshot
        existing.findings_count = findings_count
        existing.source_audit_event_id = source_audit_event_id
        session.flush()
        return _UpsertOutcome(task_id=existing.id, kind="reopened", refreshed=True)

    # Open task: reuse it. Optionally refresh the denormalised snapshot, but
    # never disturb assignment, workflow_state, or the resolution fields.
    refreshed = False
    if refresh_open_snapshots:
        existing.machine_review_status = status_snapshot
        existing.highest_severity = severity_snapshot
        existing.findings_count = findings_count
        existing.source_audit_event_id = source_audit_event_id
        session.flush()
        refreshed = True
    return _UpsertOutcome(task_id=existing.id, kind="reused", refreshed=refreshed)


def _severity_snapshot(
    summary: MachineReviewRecordSummary,
    finding: MachineReviewFinding,
) -> DBMachineReviewSeverity:
    """Record-level highest severity for the snapshot column (NOT NULL).

    Prefer the summary's record-level ``highest_severity``; fall back to the
    finding's own severity if the summary somehow carries none (it always does
    when at least one finding exists, but the column is NOT NULL so this stays
    defensive).
    """
    severity = summary.highest_severity or finding.severity
    return DBMachineReviewSeverity(severity.value)
