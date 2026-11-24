"""Submissions with their machine-review state, for the curator list.

One page of submissions, each with how many reviewer runs it has, its latest
run, and how many curator tasks exist and are still open. A constant number of
queries per page: the count, the page, the latest event of each submission, the
open job of each and the grouped task counts.

The latest run is the machine-review event with the highest
``(created_at, id)``; events written inside one transaction share a
``created_at`` so the id decides. Its payload is parsed with the same reader
the inspection uses; an unreadable one is reported as ``parsed=False``.

Read-only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, exists, func, select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.common import (
    MachineReviewCuratorTaskStateGroup,
    MachineReviewJobStatus,
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionStatus,
)
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.machine_review_job import MachineReviewJob
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review.audit_adapter import machine_review_result_from_audit_event
from app.services.machine_review.read_model import highest_severity
from app.services.machine_review.schemas import MachineReviewSeverity, MachineReviewStatus

_IS_REVIEW_EVENT = and_(
    SubmissionAuditEvent.event_kind == SubmissionAuditEventKind.llm_precheck_recorded,
    SubmissionAuditEvent.actor_kind == SubmissionActorKind.llm,
)


@dataclass(frozen=True)
class OverviewLatestRun:
    audit_event_id: int
    reviewed_at: datetime | None
    parsed: bool
    status: MachineReviewStatus | None
    highest_severity: MachineReviewSeverity | None
    findings_count: int
    model: str | None
    provider: str | None


@dataclass(frozen=True)
class OverviewOpenJob:
    id: int
    status: MachineReviewJobStatus
    step_count: int
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None


@dataclass(frozen=True)
class SubmissionOverviewRow:
    submission: Submission
    created_by_username: str | None
    run_count: int
    latest_run: OverviewLatestRun | None
    open_job: OverviewOpenJob | None
    open_task_count: int
    task_count: int


def _latest_run(event: SubmissionAuditEvent) -> OverviewLatestRun:
    parsed = machine_review_result_from_audit_event(event.details_json)
    result = parsed.result
    return OverviewLatestRun(
        audit_event_id=event.id,
        reviewed_at=event.created_at,
        parsed=result is not None,
        status=None if result is None else result.status,
        highest_severity=None if result is None else highest_severity(result.findings),
        findings_count=0 if result is None else len(result.findings),
        model=None if result is None else result.model,
        provider=parsed.provider,
    )


def list_submission_machine_review_overview(
    session: Session,
    *,
    statuses: Sequence[SubmissionStatus] | None = None,
    reviewed: bool | None = None,
    has_open_tasks: bool | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[SubmissionOverviewRow], int]:
    """One page and the exact total under the same filters, newest submitted first."""
    open_states = MachineReviewCuratorTaskStateGroup.open.states()
    filters = []
    if statuses:
        filters.append(Submission.status.in_(list(statuses)))
    if reviewed is not None:
        has_event = exists().where(SubmissionAuditEvent.submission_id == Submission.id, _IS_REVIEW_EVENT)
        filters.append(has_event if reviewed else ~has_event)
    if has_open_tasks is not None:
        has_open = exists().where(
            MachineReviewCuratorTask.submission_id == Submission.id,
            MachineReviewCuratorTask.workflow_state.in_(open_states),
        )
        filters.append(has_open if has_open_tasks else ~has_open)

    total = session.scalar(select(func.count()).select_from(Submission).where(*filters)) or 0
    page = session.execute(
        select(Submission, AppUser.username)
        .join(AppUser, AppUser.id == Submission.created_by, isouter=True)
        .where(*filters)
        .order_by(Submission.submitted_at.desc(), Submission.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    ids = [submission.id for submission, _ in page]
    if not ids:
        return [], total

    run_counts = dict(
        session.execute(
            select(SubmissionAuditEvent.submission_id, func.count())
            .where(SubmissionAuditEvent.submission_id.in_(ids), _IS_REVIEW_EVENT)
            .group_by(SubmissionAuditEvent.submission_id)
        ).all()
    )
    latest_events = {
        ev.submission_id: ev
        for ev in session.scalars(
            select(SubmissionAuditEvent)
            .where(SubmissionAuditEvent.submission_id.in_(ids), _IS_REVIEW_EVENT)
            .distinct(SubmissionAuditEvent.submission_id)
            .order_by(
                SubmissionAuditEvent.submission_id,
                SubmissionAuditEvent.created_at.desc(),
                SubmissionAuditEvent.id.desc(),
            )
        )
    }
    task_counts = {
        sid: (n_all, n_open)
        for sid, n_all, n_open in session.execute(
            select(
                MachineReviewCuratorTask.submission_id,
                func.count(),
                func.sum(case((MachineReviewCuratorTask.workflow_state.in_(open_states), 1), else_=0)),
            )
            .where(MachineReviewCuratorTask.submission_id.in_(ids))
            .group_by(MachineReviewCuratorTask.submission_id)
        )
    }
    open_jobs = {
        job.submission_id: OverviewOpenJob(
            id=job.id,
            status=job.status,
            step_count=job.step_count,
            cancel_requested=job.cancel_requested,
            created_at=job.created_at,
            started_at=job.started_at,
        )
        for job in session.scalars(
            select(MachineReviewJob).where(
                MachineReviewJob.submission_id.in_(ids),
                MachineReviewJob.status.in_(MachineReviewJobStatus.open_states()),
            )
        )
    }
    rows = []
    for submission, username in page:
        n_all, n_open = task_counts.get(submission.id, (0, 0))
        latest = latest_events.get(submission.id)
        rows.append(
            SubmissionOverviewRow(
                submission=submission,
                created_by_username=username,
                run_count=run_counts.get(submission.id, 0),
                latest_run=None if latest is None else _latest_run(latest),
                open_job=open_jobs.get(submission.id),
                open_task_count=int(n_open or 0),
                task_count=n_all,
            )
        )
    return rows, total
