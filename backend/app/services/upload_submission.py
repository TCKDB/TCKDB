"""Shared orchestration for turning a direct ``/uploads/*`` call into a
reviewable submission.

Every accepted API upload is a contribution event: it creates a
:class:`~app.db.models.submission.Submission` wrapper, runs the existing
per-family workflow under a ``not_reviewed`` :class:`ReviewPolicy` that links
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
:func:`mark_upload_ingested`, the whole transaction rolls back, and
:func:`record_failed_upload` writes the durable failure audit in a session of
its own. There is therefore no orphan-submission state to clean up on the
synchronous path.

**The audit event does not get a vote on the science.** An earlier version of
this note argued the coupling was a feature — "the whole transaction rolls
back together" — and that reasoning is what produced the 2026-08-05 incident
one line further down, where a failing idempotency receipt destroyed the
upload it receipted for. The two directions are not symmetric:

* Workflow fails → the audit event is meaningless and must roll back. Kept.
* Audit event fails → the science is already correct, already persisted, and
  irreproducible in the sense that nothing else in the system can regenerate
  it. Letting a row that merely *describes* the upload veto it inverts what
  the database is for.

So :func:`mark_upload_ingested` confines its write to a ``SAVEPOINT`` and
degrades to a loud log rather than taking the upload down with it. The
remaining audit trail — the ``submission`` row, its ``submission_created``
event, the record links and the review rows — is untouched by that
degradation, so a lost ``ingestion_succeeded`` event costs one line of history
and never a scientific record.

A submission is the audit wrapper for an upload event; it is *not* a claim of
scientific approval. ``submission.status`` stays ``pending`` (awaiting curator
review) and the records' ``record_review.status`` is ``not_reviewed``.

``not_reviewed``, and deliberately not ``under_review``. A deposit landing
says nothing about a human having picked it up, and ``under_review`` asserts
exactly that — a reviewer who does not exist, on a record with no
``reviewed_by`` and no ``reviewed_at``. The status is entered later, by a
curator, through :func:`app.services.record_review.set_record_review_status`:
the ``not_reviewed → under_review`` transition the policy table has always
permitted. Stamping it at deposit made the word describe the queue rather
than anyone's attention, and left "is anyone actually looking at this?"
unanswerable from the database.
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
    """Standard ingest policy: records await review and link to the submission."""
    return ReviewPolicy(
        status=RecordReviewStatus.not_reviewed,
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

    The returned ``policy`` is ``ReviewPolicy(status=not_reviewed,
    submission_id=..., link_records=True)`` — pass it to the per-family
    workflow so every produced record is initialised as awaiting review and
    linked to the submission. Call :func:`mark_upload_ingested` only after the
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
        status=RecordReviewStatus.not_reviewed,
        submission_id=submission.id,
        link_records=True,
    )
    return UploadSubmissionContext(submission=submission, policy=policy, kind=kind)


def mark_upload_ingested(
    session: Session,
    sub: UploadSubmissionContext,
    *,
    summary: Optional[str] = None,
) -> bool:
    """Append the ``ingestion_succeeded`` audit event for a finished upload.

    Status is unchanged (``pending``): successful ingestion is not scientific
    approval.

    Returns ``True`` when the event was appended, ``False`` when it could not
    be and was skipped. **Never raises** for a reason confined to the event
    itself — see the module docstring for why a description must not be able
    to veto the thing it describes.

    The isolation has the same two parts as
    :func:`app.services.idempotency.write_receipt_isolated`, which sits one
    line below this in all eleven synchronous upload routes:

    1. **Flush first**, so every pending scientific row is INSERTed *outside*
       the savepoint. Without this, anything the workflow had not yet flushed
       would be swept into the savepoint and rolled back alongside a failing
       audit event — the same data loss through a subtler door.
    2. **Savepoint around the event alone.** On failure the savepoint rolls
       back, the outer transaction and its payload survive, and the session
       stays usable for the rest of the request (``idem.record`` still runs).

    On success the savepoint is released and the event commits with the
    payload exactly as before.

    This has not yet fired in production only because ``summary`` is currently
    a server-built f-string. It is not a server-only field: the signature
    accepts arbitrary ``summary`` text, and
    :func:`app.services.submission.mark_ingestion_succeeded` accepts arbitrary
    ``details_json`` — ``Text`` and ``JSONB``, the same two column types that
    failed in the incident.
    """
    text = summary or f"Ingested {sub.kind.value} upload via direct API."

    # Part 1: get the payload out of the savepoint's blast radius.
    session.flush()

    # Part 2: the audit event, and only the audit event, inside the savepoint.
    savepoint = session.begin_nested()
    try:
        mark_ingestion_succeeded(
            session,
            submission=sub.submission,
            summary=text,
        )
    except Exception as exc:
        savepoint.rollback()
        logger.error(
            "ingestion_succeeded audit event could not be written for "
            "submission_id=%s (kind=%s): %s. The upload itself succeeded and "
            "its scientific records, review rows and record links are intact; "
            "only this line of the submission's audit history is missing.",
            sub.submission_id,
            sub.kind.value,
            type(exc).__name__,
            exc_info=exc,
        )
        return False

    savepoint.commit()
    return True


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


#: Key under which a sync upload route parks its failure-audit intent on the
#: write session, so ``get_write_db`` can finish the job the decorator cannot
#: reach. See :func:`audit_sync_upload_failure`.
SYNC_UPLOAD_AUDIT_KEY = "sync_upload_failure_audit"


def audit_sync_upload_failure(kind: SubmissionKind) -> Callable:
    """Decorator: durably audit a synchronous upload route's failures.

    Wraps an authenticated ``/uploads/*`` handler so that any exception
    raised after request parsing/auth records a durable failed submission
    (see :func:`record_failed_upload`) before propagating — the scientific
    transaction still rolls back atomically. The handler must take a
    ``current_user`` keyword (every upload route does).

    **The blind spot this closes.** A decorator can only observe what happens
    inside the function it wraps, and the route function is not where an
    upload finishes. ``get_write_db`` commits in dependency *teardown*, after
    the route has returned — so a commit-time failure raises outside this
    ``try`` entirely and used to leave **no failed-upload audit row for
    precisely the failure class that matters most**: the one where the
    response was already determined and the work is already gone. The
    2026-08-05 incident was exactly such a failure, and it is invisible in the
    audit tables for exactly this reason.

    The intent (who, what kind) is therefore parked on the write session under
    :data:`SYNC_UPLOAD_AUDIT_KEY` *before* the handler runs, so
    :func:`app.api.deps.get_write_db` can record the audit for a failure this
    wrapper never sees. ``audited`` guards against both paths recording the
    same failure twice.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = kwargs.get("current_user")
            session = kwargs.get("session")
            state: Optional[dict] = None
            if user is not None and isinstance(session, Session):
                state = {
                    "created_by": user.id,
                    "kind": kind,
                    "audited": False,
                }
                session.info[SYNC_UPLOAD_AUDIT_KEY] = state
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if user is not None:
                    if state is not None:
                        state["audited"] = True
                    record_failed_upload(
                        created_by=user.id,
                        kind=kind,
                        error_summary=f"{type(exc).__name__}: {exc}",
                    )
                raise

        return wrapper

    return decorator


def audit_upload_failure_at_commit(session: Session, exc: BaseException) -> None:
    """Record the failure audit for an upload that died after the route returned.

    Called from :func:`app.api.deps.get_write_db`'s error path, which is the
    only place that can see a commit-time failure. No-ops for every session
    that is not a decorated synchronous upload, and for one whose failure the
    decorator already audited.

    Best-effort by construction — :func:`record_failed_upload` swallows its
    own errors — so this can never mask the exception on its way out.
    """
    state = session.info.get(SYNC_UPLOAD_AUDIT_KEY)
    if not isinstance(state, dict) or state.get("audited"):
        return
    state["audited"] = True
    logger.error(
        "Synchronous %s upload failed at commit, after the route returned "
        "(%s). Nothing was stored; recording the failed-upload audit that the "
        "route decorator could not reach.",
        state["kind"].value,
        type(exc).__name__,
        exc_info=exc,
    )
    record_failed_upload(
        created_by=state["created_by"],
        kind=state["kind"],
        error_summary=f"{type(exc).__name__}: {exc}",
    )


__all__ = [
    "SYNC_UPLOAD_AUDIT_KEY",
    "UploadSubmissionContext",
    "audit_sync_upload_failure",
    "audit_upload_failure_at_commit",
    "mark_upload_ingested",
    "open_job_submission",
    "open_upload_submission",
    "record_failed_upload",
    "review_policy_for_submission",
    "submission_kind_for_job_kind",
]
