"""Queue fresh machine reviews for submissions whose latest review is in a previous format.

A stored review in a format this release no longer reads shows as unparsed.
:func:`find_submissions_with_previous_format_review` lists the submissions
whose newest review is in such a format; :func:`queue_reruns` queues one job
each through the ordinary job service, so the switches, the open-job guard and
the requester are recorded as for any other request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.config import settings as app_settings
from app.db.models.app_user import AppUser
from app.db.models.common import (
    MachineReviewProviderKey,
    SubmissionActorKind,
    SubmissionAuditEventKind,
)
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review import jobs
from app.services.machine_review.schemas import MACHINE_REVIEW_FORMAT


def find_submissions_with_previous_format_review(
    session: Session, *, format_version: int = MACHINE_REVIEW_FORMAT.version - 1
) -> list[Submission]:
    """Submissions whose newest machine-review event is in ``format_version``, oldest first."""
    newest = session.scalars(
        select(SubmissionAuditEvent)
        .where(
            SubmissionAuditEvent.event_kind == SubmissionAuditEventKind.llm_precheck_recorded,
            SubmissionAuditEvent.actor_kind == SubmissionActorKind.llm,
        )
        .distinct(SubmissionAuditEvent.submission_id)
        .order_by(
            SubmissionAuditEvent.submission_id,
            SubmissionAuditEvent.created_at.desc(),
            SubmissionAuditEvent.id.desc(),
        )
    ).all()
    wanted = {"name": MACHINE_REVIEW_FORMAT.name, "version": format_version}
    ids = [
        event.submission_id
        for event in newest
        if isinstance(event.details_json, dict) and event.details_json.get("format") == wanted
    ]
    if not ids:
        return []
    return list(session.scalars(select(Submission).where(Submission.id.in_(ids)).order_by(Submission.id)))


@dataclass
class RerunReport:
    """What :func:`queue_reruns` did, by submission ``public_ref``."""

    queued: list[str] = field(default_factory=list)
    already_open: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)


def queue_reruns(
    session: Session,
    submissions: list[Submission],
    *,
    requester: AppUser,
    provider_key: MachineReviewProviderKey,
    settings_obj: Any = app_settings,
) -> RerunReport:
    """Queue one job per submission; report the ones skipped or refused. Commit stays with the caller."""
    report = RerunReport()
    for submission in submissions:
        try:
            jobs.create_job(
                session,
                submission_id=submission.id,
                requester=requester,
                provider_key=provider_key,
                settings_obj=settings_obj,
            )
        except jobs.MachineReviewJobConflict:
            report.already_open.append(submission.public_ref)
        except jobs.MachineReviewUnavailable as exc:
            report.refused[submission.public_ref] = exc.reason
        else:
            report.queued.append(submission.public_ref)
    return report


__all__ = ["RerunReport", "find_submissions_with_previous_format_review", "queue_reruns"]
