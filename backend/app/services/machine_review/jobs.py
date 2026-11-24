"""Machine-review job lifecycle: create, claim, progress, cancel, finish.

Every function takes a session, flushes and never commits; the caller owns the
transaction. State changes that end a job go through :func:`finish_job`, which
is fenced on the attempt number so a worker that lost its lease cannot write
over a job another worker has since claimed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.config import settings as app_settings
from app.api.errors import MachineReviewJobError, NotFoundError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    MachineReviewAvailability,
    MachineReviewExecutor,
    MachineReviewJobEventKind,
    MachineReviewJobFailure,
    MachineReviewJobStatus,
    MachineReviewProviderKey,
)
from app.db.models.machine_review_job import MachineReviewJob, MachineReviewJobEvent
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_PROMPT
from app.services.machine_review.run import _clip, _configured_model, _redact_secrets
from app.services.machine_review.switches import MachineReviewSwitches, read_machine_review_switches

LEASE_DURATION = timedelta(minutes=5)
_OPEN_INDEX = "ux_machine_review_job_open_submission"
_MAX_FAILURE_DETAIL_CHARS = 2000
_FAKE_MODEL = "fake_test/machine-review"

_REMOTE_KEYS = frozenset({MachineReviewProviderKey.claude_code, MachineReviewProviderKey.codex})
MODE_FOR_KEY = {
    MachineReviewProviderKey.fake: "test",
    MachineReviewProviderKey.cloud: "cloud",
    MachineReviewProviderKey.local: "local",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


CONFLICT_CODE = "machine_review_job_conflict"
NOT_CANCELLABLE_CODE = "machine_review_job_not_cancellable"
NOT_OWNED_CODE = "machine_review_job_not_owned"


class MachineReviewJobConflict(MachineReviewJobError):
    """The submission already has a queued or running job; ``open_job`` is that job."""

    def __init__(self, open_job: MachineReviewJob) -> None:
        super().__init__(
            "This submission already has a machine review queued or running.",
            status=409,
            code=CONFLICT_CODE,
            context={
                "open_job": {
                    "status": open_job.status.value,
                    "provider_key": open_job.provider_key.value,
                    "step_count": open_job.step_count,
                    "cancel_requested": open_job.cancel_requested,
                    "created_at": open_job.created_at.isoformat(),
                    "started_at": open_job.started_at.isoformat() if open_job.started_at else None,
                }
            },
        )
        self.open_job = open_job


_UNAVAILABLE: dict[str, tuple[int, str]] = {
    "mode_off": (409, "machine_review_mode_off"),
    "machine_review_disabled": (409, "machine_review_disabled"),
    "curator_requests_disabled": (403, "machine_review_curator_requests_disabled"),
    "not_allowed_for_user": (403, "machine_review_not_allowed_for_user"),
    "provider_not_configured": (409, "machine_review_provider_not_configured"),
    "remote_runner_unavailable": (409, "machine_review_remote_runner_unavailable"),
}


class MachineReviewUnavailable(MachineReviewJobError):
    """A review cannot be requested; ``reason`` is a :class:`MachineReviewAvailability` value other than ``ok``."""

    REASONS = tuple(a.value for a in MachineReviewAvailability if a is not MachineReviewAvailability.ok)

    def __init__(self, reason: str) -> None:
        if reason not in self.REASONS:
            raise ValueError(f"Unknown machine review unavailable reason: {reason!r}")
        status, code = _UNAVAILABLE[reason]
        super().__init__(f"Machine review is unavailable: {reason}.", status=status, code=code)
        self.reason = reason


class MachineReviewJobNotCancellable(MachineReviewJobError):
    """The job has already ended."""

    def __init__(self, message: str = "The machine review job has already ended.") -> None:
        super().__init__(message, status=409, code=NOT_CANCELLABLE_CODE)


class MachineReviewJobNotOwned(MachineReviewJobError):
    """A curator tried to cancel a job another user requested."""

    def __init__(self) -> None:
        super().__init__("Only the requester or an admin may cancel this machine review job.", status=403, code=NOT_OWNED_CODE)


def provider_availability(
    session: Session,
    requester: AppUser,
    provider_key: MachineReviewProviderKey,
    settings_obj: Any = app_settings,
) -> MachineReviewAvailability:
    """Whether ``requester`` may ask ``provider_key`` for a review now; :func:`create_job` refuses on the same answer."""
    return _availability(read_machine_review_switches(session, settings_obj), requester, provider_key, settings_obj)


def list_provider_availability(
    session: Session, requester: AppUser, settings_obj: Any = app_settings
) -> dict[MachineReviewProviderKey, MachineReviewAvailability]:
    """The availability of every provider key for ``requester``, in enum order."""
    switches = read_machine_review_switches(session, settings_obj)
    return {key: _availability(switches, requester, key, settings_obj) for key in MachineReviewProviderKey}


def curator_switch_refuses(switches: MachineReviewSwitches, requester: AppUser) -> bool:
    """Whether the curator switch is off and ``requester`` is a curator; an admin is never refused by it."""
    return requester.role is AppUserRole.curator and not switches.curator_requests_enabled


def _availability(
    switches: MachineReviewSwitches,
    requester: AppUser,
    provider_key: MachineReviewProviderKey,
    settings_obj: Any,
) -> MachineReviewAvailability:
    if switches.mode_off:
        return MachineReviewAvailability.mode_off
    if requester.role not in (AppUserRole.curator, AppUserRole.admin):
        return MachineReviewAvailability.not_allowed_for_user
    if not switches.machine_review_enabled:
        return MachineReviewAvailability.machine_review_disabled
    if curator_switch_refuses(switches, requester):
        return MachineReviewAvailability.curator_requests_disabled
    if provider_key in _REMOTE_KEYS:
        if requester.role is not AppUserRole.admin:
            return MachineReviewAvailability.not_allowed_for_user
        return MachineReviewAvailability.remote_runner_unavailable
    if settings_obj.ai_review_assistant_mode != MODE_FOR_KEY[provider_key] or (
        provider_key is MachineReviewProviderKey.fake and settings_obj.deployment_mode != "local"
    ):
        return MachineReviewAvailability.provider_not_configured
    return MachineReviewAvailability.ok


def get_open_job(session: Session, submission_id: int) -> MachineReviewJob | None:
    """Return the queued or running job of a submission, if any."""
    return session.scalar(
        select(MachineReviewJob).where(
            MachineReviewJob.submission_id == submission_id,
            MachineReviewJob.status.in_(MachineReviewJobStatus.open_states()),
        )
    )


def get_job(session: Session, job_id: int) -> MachineReviewJob:
    """Return one job, or raise the coded not-found."""
    job = session.get(MachineReviewJob, job_id)
    if job is None:
        raise NotFoundError("Machine review job not found.", code="machine_review_job_not_found")
    return job


def list_jobs(session: Session, submission_id: int, *, offset: int = 0, limit: int = 50) -> tuple[list[MachineReviewJob], int]:
    """A submission's jobs, newest first, with the total; an open job is always first."""
    total = session.scalar(
        select(func.count()).select_from(MachineReviewJob).where(MachineReviewJob.submission_id == submission_id)
    )
    rows = session.scalars(
        select(MachineReviewJob)
        .where(MachineReviewJob.submission_id == submission_id)
        .order_by(MachineReviewJob.created_at.desc(), MachineReviewJob.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return list(rows), int(total or 0)


def list_events(session: Session, job_id: int, *, after_seq: int = 0) -> list[MachineReviewJobEvent]:
    """A job's progress entries with ``seq`` greater than ``after_seq``, in order."""
    return list(
        session.scalars(
            select(MachineReviewJobEvent)
            .where(MachineReviewJobEvent.job_id == job_id, MachineReviewJobEvent.seq > after_seq)
            .order_by(MachineReviewJobEvent.seq)
        )
    )


def create_job(
    session: Session,
    *,
    submission_id: int,
    requester: AppUser,
    provider_key: MachineReviewProviderKey,
    settings_obj: Any = app_settings,
) -> MachineReviewJob:
    """Queue a machine review, or raise why it cannot be queued.

    Raises :class:`MachineReviewUnavailable` when the switches, the requester's
    role or the provider key refuse, and :class:`MachineReviewJobConflict` when
    the submission already has an open job.
    """
    if session.get(Submission, submission_id) is None:
        raise NotFoundError("Submission not found.")

    availability = provider_availability(session, requester, provider_key, settings_obj)
    if availability is not MachineReviewAvailability.ok:
        raise MachineReviewUnavailable(availability.value)

    existing = get_open_job(session, submission_id)
    if existing is not None:
        raise MachineReviewJobConflict(existing)

    job = MachineReviewJob(
        submission_id=submission_id,
        requested_by=requester.id,
        provider_key=provider_key,
        executor=MachineReviewExecutor.in_process,
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError as exc:
        if _OPEN_INDEX not in str(exc.orig):
            raise
        winner = get_open_job(session, submission_id)
        if winner is None:
            raise
        raise MachineReviewJobConflict(winner) from exc
    return job


def append_event(
    session: Session,
    job: MachineReviewJob,
    kind: MachineReviewJobEventKind,
    *,
    tool_name: str | None = None,
    outcome: str | None = None,
    started_at: datetime | None = None,
    duration_ms: int | None = None,
    detail: dict[str, Any] | None = None,
    settings_obj: Any = app_settings,
) -> MachineReviewJobEvent:
    """Append the next numbered progress entry; the payload is kept only when full-context storage is on."""
    session.execute(select(MachineReviewJob.id).where(MachineReviewJob.id == job.id).with_for_update())
    seq = (
        session.scalar(
            select(func.coalesce(func.max(MachineReviewJobEvent.seq), 0)).where(
                MachineReviewJobEvent.job_id == job.id
            )
        )
        or 0
    ) + 1
    event = MachineReviewJobEvent(
        job_id=job.id,
        seq=seq,
        kind=kind,
        tool_name=tool_name,
        status_code=outcome,
        started_at=started_at,
        duration_ms=duration_ms,
        detail_json=detail if settings_obj.llm_precheck_store_full_context else None,
    )
    session.add(event)
    session.flush()
    return event


def claim_next_job(
    session: Session,
    *,
    executor: MachineReviewExecutor = MachineReviewExecutor.in_process,
    now: datetime | None = None,
    settings_obj: Any = app_settings,
) -> MachineReviewJob | None:
    """Claim the oldest queued job for ``executor``; concurrent claimers skip locked rows."""
    now = now or _utcnow()
    job = session.scalar(
        select(MachineReviewJob)
        .where(
            MachineReviewJob.status == MachineReviewJobStatus.queued,
            MachineReviewJob.executor == executor,
        )
        .order_by(MachineReviewJob.created_at, MachineReviewJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = MachineReviewJobStatus.running
    job.attempts += 1
    job.started_at = job.started_at or now
    job.heartbeat_at = now
    job.lease_expires_at = now + LEASE_DURATION
    job.model = (
        _FAKE_MODEL if job.provider_key is MachineReviewProviderKey.fake else _configured_model(settings_obj)
    )
    job.prompt_name = ACTIVE_MACHINE_REVIEW_PROMPT.name
    job.prompt_version = ACTIVE_MACHINE_REVIEW_PROMPT.version
    job.budget_json = {
        "max_input_tokens": settings_obj.llm_precheck_max_input_tokens,
        "max_output_tokens": settings_obj.llm_precheck_max_output_tokens,
        "timeout_seconds": settings_obj.llm_precheck_timeout_seconds,
    }
    session.flush()
    append_event(
        session, job, MachineReviewJobEventKind.claimed, outcome="ok", started_at=now, settings_obj=settings_obj
    )
    return job


def heartbeat(session: Session, job_id: int, attempt: int, now: datetime | None = None) -> bool:
    """Extend the lease of a running job; False when the caller no longer holds it."""
    now = now or _utcnow()
    job = session.scalar(
        select(MachineReviewJob)
        .where(MachineReviewJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.status is not MachineReviewJobStatus.running or job.attempts != attempt:
        return False
    job.heartbeat_at = now
    job.lease_expires_at = now + LEASE_DURATION
    session.flush()
    return True


def reap_expired(session: Session, now: datetime | None = None) -> int:
    """Requeue running jobs whose lease expired (cancel them if a cancel was requested), or fail them as ``worker_lost`` once attempts run out."""
    now = now or _utcnow()
    jobs = session.scalars(
        select(MachineReviewJob)
        .where(
            MachineReviewJob.status == MachineReviewJobStatus.running,
            MachineReviewJob.lease_expires_at < now,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for job in jobs:
        job.lease_expires_at = None
        job.heartbeat_at = None
        if job.cancel_requested:
            job.status = MachineReviewJobStatus.cancelled
            job.finished_at = now
            session.flush()
            append_event(session, job, MachineReviewJobEventKind.finished, outcome="cancelled", started_at=now)
        elif job.attempts < job.max_attempts:
            job.status = MachineReviewJobStatus.queued
            session.flush()
            append_event(session, job, MachineReviewJobEventKind.retry, outcome="lease_expired", started_at=now)
        else:
            job.status = MachineReviewJobStatus.failed
            job.failure_code = MachineReviewJobFailure.worker_lost
            job.finished_at = now
            session.flush()
            append_event(session, job, MachineReviewJobEventKind.finished, outcome="failed", started_at=now)
    session.flush()
    return len(jobs)


def request_cancel(session: Session, job_id: int, *, actor: AppUser | None = None) -> MachineReviewJob:
    """Cancel a queued job now, or ask a running job to stop at its next step.

    When ``actor`` is given, a non-admin may cancel only a job they requested.
    """
    job = session.scalar(
        select(MachineReviewJob)
        .where(MachineReviewJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise NotFoundError("Machine review job not found.", code="machine_review_job_not_found")
    if actor is not None and actor.role is not AppUserRole.admin and job.requested_by != actor.id:
        raise MachineReviewJobNotOwned()
    if job.status is MachineReviewJobStatus.queued:
        now = _utcnow()
        job.status = MachineReviewJobStatus.cancelled
        job.cancel_requested = True
        job.finished_at = now
        session.flush()
        append_event(session, job, MachineReviewJobEventKind.finished, outcome="cancelled", started_at=now)
    elif job.status is MachineReviewJobStatus.running:
        job.cancel_requested = True
        session.flush()
    else:
        raise MachineReviewJobNotCancellable()
    return job


def is_cancel_requested(session: Session, job_id: int) -> bool:
    """Read the cancel flag fresh from the database."""
    return bool(
        session.scalar(
            select(MachineReviewJob.cancel_requested)
            .where(MachineReviewJob.id == job_id)
            .execution_options(populate_existing=True)
        )
    )


def finish_job(
    session: Session,
    job_id: int,
    attempt: int,
    *,
    status: MachineReviewJobStatus,
    failure_code: MachineReviewJobFailure | None = None,
    failure_detail: str | None = None,
    audit_event: SubmissionAuditEvent | None = None,
    settings_obj: Any = app_settings,
) -> bool:
    """End a running job; the fence.

    Returns False and writes nothing unless the job is still running under
    ``attempt``, so a worker whose lease was reaped cannot finish the job.
    """
    job = session.scalar(
        select(MachineReviewJob)
        .where(MachineReviewJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.status is not MachineReviewJobStatus.running or job.attempts != attempt:
        return False
    now = _utcnow()
    job.status = status
    job.failure_code = failure_code
    job.failure_detail = (
        _clip(_redact_secrets(failure_detail, settings_obj), _MAX_FAILURE_DETAIL_CHARS) if failure_detail else None
    )
    job.audit_event_id = audit_event.id if audit_event is not None else None
    job.finished_at = now
    job.lease_expires_at = None
    session.flush()
    append_event(
        session, job, MachineReviewJobEventKind.finished, outcome=status.value, started_at=now,
        settings_obj=settings_obj,
    )
    return True


__all__ = [
    "LEASE_DURATION",
    "MODE_FOR_KEY",
    "MachineReviewJobConflict",
    "MachineReviewJobError",
    "MachineReviewJobNotCancellable",
    "MachineReviewJobNotOwned",
    "MachineReviewUnavailable",
    "append_event",
    "claim_next_job",
    "curator_switch_refuses",
    "create_job",
    "finish_job",
    "get_job",
    "get_open_job",
    "heartbeat",
    "is_cancel_requested",
    "list_events",
    "list_jobs",
    "list_provider_availability",
    "provider_availability",
    "reap_expired",
    "request_cancel",
]
