"""Shared orchestration for turning a direct ``/uploads/*`` call into a
reviewable submission.

Every accepted API upload is a contribution event: it creates a
:class:`~app.db.models.submission.Submission` wrapper, runs the existing
per-family workflow under an ``under_review`` :class:`ReviewPolicy` that links
every produced record back to the submission, and appends an
``ingestion_succeeded`` audit event on success.

Usage in a route (flat, exception-safe by ordering)::

    sub = open_upload_submission(session, created_by=user.id,
                                 kind=SubmissionKind.conformer)
    outcome = persist_conformer_upload(
        session, request, created_by=user.id, review_policy=sub.policy
    )
    result = ConformerUploadResult(..., submission_id=sub.submission_id)
    mark_upload_ingested(session, sub)
    idem.record(...)

Transaction management stays with the route's ``get_write_db`` dependency. If
the wrapped workflow raises, control never reaches
:func:`mark_upload_ingested`, and the whole transaction — submission, audit,
record links, review rows, and scientific records — rolls back together. There
is therefore no orphan-submission state to clean up on the synchronous path,
and ``ingestion_failed`` is reserved for a future async/two-phase path that can
commit a failure record independently.

A submission is the audit wrapper for an upload event; it is *not* a claim of
scientific approval. ``submission.status`` stays ``pending`` (awaiting curator
review) and the records' ``record_review.status`` is ``under_review``.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.db.models.common import (
    RecordReviewStatus,
    SubmissionKind,
    SubmissionSourceKind,
    SubmissionStatus,
    UploadJobKind,
)
from app.db.models.submission import Submission
from app.services.record_review import ReviewPolicy
from app.services.submission import (
    create_submission,
    mark_ingestion_failed,
    mark_ingestion_succeeded,
)

logger = logging.getLogger(__name__)


def review_policy_for_submission(submission: Submission) -> ReviewPolicy:
    """Standard ingest policy: records enter review and link to the submission."""
    return ReviewPolicy(
        status=RecordReviewStatus.under_review,
        submission_id=submission.id,
        link_records=True,
    )


def submission_kind_for_job_kind(job_kind: UploadJobKind) -> SubmissionKind:
    """Map an async ``UploadJobKind`` onto the submission-layer classification.

    The token vocabularies are aligned (every ``UploadJobKind`` value is a
    valid ``SubmissionKind``), so this is a direct value mapping.
    """
    return SubmissionKind(job_kind.value)


def open_job_submission(
    session: Session,
    *,
    created_by: int | None,
    job_kind: UploadJobKind,
    upload_job_id: str,
) -> Submission:
    """Create the submission wrapper for an enqueued async upload job.

    Called at enqueue time so the contribution event is auditable from the
    moment it is accepted for processing — even if the worker later fails or
    never runs. The worker links records / flips audit state against this
    submission via its ``upload_job_id``.
    """
    return create_submission(
        session,
        created_by=created_by,
        submission_kind=submission_kind_for_job_kind(job_kind),
        source_kind=SubmissionSourceKind.api,
        upload_job_id=upload_job_id,
        title=f"Async {job_kind.value} upload",
    )


@dataclass
class UploadSubmissionContext:
    """Handle returned to an upload route for its submission scope."""

    submission: Submission
    policy: ReviewPolicy
    kind: SubmissionKind

    @property
    def submission_id(self) -> int:
        return self.submission.id


def open_upload_submission(
    session: Session,
    *,
    created_by: int,
    kind: SubmissionKind,
    title: Optional[str] = None,
    summary: Optional[str] = None,
) -> UploadSubmissionContext:
    """Create the submission shell and the review policy for one upload.

    The returned ``policy`` is ``ReviewPolicy(status=under_review,
    submission_id=..., link_records=True)`` — pass it to the per-family
    workflow so every produced record is initialised under review and linked
    to the submission. Call :func:`mark_upload_ingested` only after the
    workflow returns successfully.
    """
    submission = create_submission(
        session,
        created_by=created_by,
        submission_kind=kind,
        source_kind=SubmissionSourceKind.api,
        title=title,
        summary=summary,
    )
    policy = ReviewPolicy(
        status=RecordReviewStatus.under_review,
        submission_id=submission.id,
        link_records=True,
    )
    return UploadSubmissionContext(submission=submission, policy=policy, kind=kind)


def mark_upload_ingested(
    session: Session,
    sub: UploadSubmissionContext,
    *,
    summary: Optional[str] = None,
) -> None:
    """Append the ``ingestion_succeeded`` audit event for a finished upload.

    Status is unchanged (``pending``): successful ingestion is not scientific
    approval.
    """
    mark_ingestion_succeeded(
        session,
        submission=sub.submission,
        summary=summary or f"Ingested {sub.kind.value} upload via direct API.",
    )


# ---------------------------------------------------------------------------
# Durable failed-ingestion audit (synchronous uploads)
# ---------------------------------------------------------------------------


def record_failed_upload(
    *,
    created_by: int,
    kind: SubmissionKind,
    error_summary: str,
    session_factory: Optional[Callable[[], Session]] = None,
) -> Optional[int]:
    """Durably record a failed synchronous upload in its own transaction.

    A synchronous ``/uploads/*`` failure rolls back its scientific
    persistence atomically (no partial records) — which also discards the
    submission opened for the attempt. To still answer "who attempted what,
    when, on which route, and why did it fail", this opens a *fresh* session
    (independent of the request's rolled-back transaction) and writes:

    * a ``submission`` with ``status=failed`` (system terminal state),
    * a ``submission_created`` audit event,
    * an ``ingestion_failed`` audit event with the error summary.

    It creates **no** scientific records, record links, or review rows. It is
    best-effort: any error here is logged and swallowed so the failure audit
    never masks the original upload error. Only payloads that already passed
    authentication and request parsing reach this path; invalid payloads are
    rejected by FastAPI before the route body and never create a submission.

    Returns the failed submission id, or ``None`` if recording itself failed.
    """
    if session_factory is None:
        # Lazy import keeps this service free of an app-layer import at module
        # load time.
        from app.api.deps import SessionLocal as session_factory  # type: ignore

    try:
        with session_factory() as session:
            with session.begin():
                submission = create_submission(
                    session,
                    created_by=created_by,
                    submission_kind=kind,
                    source_kind=SubmissionSourceKind.api,
                    title=f"Failed {kind.value} upload",
                )
                mark_ingestion_failed(
                    session,
                    submission=submission,
                    reason=error_summary,
                )
                submission.status = SubmissionStatus.failed
                submission_id = submission.id
            return submission_id
    except Exception:  # pragma: no cover - audit must never mask the real error
        logger.exception("failed to record failed-upload audit (kind=%s)", kind)
        return None


def audit_sync_upload_failure(kind: SubmissionKind) -> Callable:
    """Decorator: durably audit a synchronous upload route's failures.

    Wraps an authenticated ``/uploads/*`` handler so that any exception
    raised after request parsing/auth records a durable failed submission
    (see :func:`record_failed_upload`) before propagating — the scientific
    transaction still rolls back atomically. The handler must take a
    ``current_user`` keyword (every upload route does).
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                user = kwargs.get("current_user")
                if user is not None:
                    record_failed_upload(
                        created_by=user.id,
                        kind=kind,
                        error_summary=f"{type(exc).__name__}: {exc}",
                    )
                raise

        return wrapper

    return decorator


__all__ = [
    "UploadSubmissionContext",
    "audit_sync_upload_failure",
    "mark_upload_ingested",
    "open_job_submission",
    "open_upload_submission",
    "record_failed_upload",
    "review_policy_for_submission",
    "submission_kind_for_job_kind",
]
