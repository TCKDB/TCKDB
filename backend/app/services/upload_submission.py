"""Shared orchestration for turning a direct ``/uploads/*`` call into a
reviewable submission.

Every accepted API upload is a contribution event: it creates a
:class:`~app.db.models.submission.Submission` wrapper, runs the existing
per-family workflow under a ``not_reviewed`` :class:`ReviewPolicy` that links
every produced record back to the submission, and appends an
``ingestion_succeeded`` audit event on success.

Usage in a route (flat, exception-safe by ordering)::

    sub = open_upload_submission(session, created_by=user.id,
                                 kind=SubmissionKind.conformer,
                                 rights=request.rights)
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
from typing import Any, Callable, Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from tckdb_schemas.rights import DepositRights

from app.db.models.app_user import AppUser
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionSourceKind,
    SubmissionStatus,
    UploadJobKind,
)
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.record_review import ReviewPolicy
from app.services.rights import attest_from_deposit
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
    rights: DepositRights | None,
) -> Submission:
    """Create the submission wrapper for an enqueued async upload job.

    Called at enqueue time so the contribution event is auditable from the
    moment it is accepted for processing — even if the worker later fails or
    never runs. The worker links records / flips audit state against this
    submission via its ``upload_job_id``.

    ``rights`` is the upload's deposit-time license agreement and is attested
    here, at enqueue, for the same reason the submission is opened here: the
    agreement was made when the deposit was accepted, not when a worker got
    round to it. Keyword-only and **without a default**, so a route that
    forgets to pass it fails at the call rather than silently depositing
    unlicensed records.
    """
    submission = create_submission(
        session,
        created_by=created_by,
        submission_kind=submission_kind_for_job_kind(job_kind),
        source_kind=SubmissionSourceKind.api,
        upload_job_id=upload_job_id,
        title=f"Async {job_kind.value} upload",
    )
    _attest_deposit(session, submission, rights)
    return submission


def _attest_deposit(
    session: Session, submission: Submission, rights: DepositRights | None
) -> None:
    """Record the deposit's rights fragment against its freshly opened submission.

    The actor is always the submission's creator: ``create_submission`` has
    just verified that user exists, and a depositor agreement is the
    depositor's own statement.
    """
    if rights is None:
        return
    actor = session.get(AppUser, submission.created_by)
    attest_from_deposit(session, submission=submission, rights=rights, actor=actor)


@dataclass
class UploadSubmissionContext:
    """Handle returned to an upload route for its submission scope."""

    submission: Submission
    policy: ReviewPolicy
    kind: SubmissionKind

    @property
    def submission_id(self) -> int:
        return self.submission.id

    @property
    def submission_ref(self) -> str:
        """The ``sub_`` ref, minted when the row was flushed."""
        return self.submission.public_ref


def open_upload_submission(
    session: Session,
    *,
    created_by: int,
    kind: SubmissionKind,
    rights: DepositRights | None,
    title: Optional[str] = None,
    summary: Optional[str] = None,
) -> UploadSubmissionContext:
    """Create the submission shell and the review policy for one upload.

    The returned ``policy`` is ``ReviewPolicy(status=not_reviewed,
    submission_id=..., link_records=True)`` — pass it to the per-family
    workflow so every produced record is initialised as awaiting review and
    linked to the submission. Call :func:`mark_upload_ingested` only after the
    workflow returns successfully.

    ``rights`` is the request's deposit-time license agreement (``None`` when
    the client sent none) and is recorded as a ``depositor_agreement``
    attestation by the depositor. Keyword-only and **without a default** on
    purpose: every one of the upload routes must state what it passes, so a
    new route cannot forget the question and quietly take deposits nobody
    licensed.
    """
    submission = create_submission(
        session,
        created_by=created_by,
        submission_kind=kind,
        source_kind=SubmissionSourceKind.api,
        title=title,
        summary=summary,
    )
    _attest_deposit(session, submission, rights)
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


@dataclass(frozen=True)
class FailedUploadKey:
    """What identifies a retry of one keyed upload: route and idempotency key.

    The credential is the third leg, and is the ``created_by`` user passed to
    :func:`record_failed_upload` -- the same scope the idempotency table
    itself uses (``user_id``, method, endpoint, key).
    """

    route: str
    idempotency_key: str
    payload_hash: Optional[str] = None

    @classmethod
    def from_context(cls, idem: Any) -> Optional["FailedUploadKey"]:
        """Build from an ``IdempotencyContext``; ``None`` when no key was sent."""
        if idem is None or not getattr(idem, "enabled", False):
            return None
        if not idem.key or not idem.endpoint or not idem.method:
            return None
        return cls(
            route=f"{idem.method} {idem.endpoint}",
            idempotency_key=idem.key,
            payload_hash=idem.payload_hash,
        )


def _find_failed_submission(
    session: Session,
    *,
    created_by: int,
    kind: SubmissionKind,
    key: FailedUploadKey,
) -> Optional[Submission]:
    """The earliest failed submission recorded for this (user, route, key)."""
    return session.scalars(
        select(Submission)
        .join(SubmissionAuditEvent, SubmissionAuditEvent.submission_id == Submission.id)
        .where(
            Submission.created_by == created_by,
            Submission.submission_kind == kind,
            Submission.status == SubmissionStatus.failed,
            SubmissionAuditEvent.event_kind == SubmissionAuditEventKind.ingestion_failed,
            SubmissionAuditEvent.details_json.contains(
                {"route": key.route, "idempotency_key": key.idempotency_key}
            ),
        )
        .order_by(Submission.id)
        .limit(1)
    ).first()


def record_failed_upload(
    *,
    created_by: int,
    kind: SubmissionKind,
    error_summary: str,
    session_factory: Optional[Callable[[], Session]] = None,
    retry_key: Optional[FailedUploadKey] = None,
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

    **Retries.** When the request carried an ``Idempotency-Key`` (``retry_key``),
    a refused retry is the same contribution event again, so it does not open a
    second submission. The first failure opens the submission as above and
    stamps its ``ingestion_failed`` event with ``route``, ``idempotency_key``,
    ``payload_hash`` and ``attempt = 1``. A later failure for the same
    (``created_by``, route, key) finds that submission and *appends* one more
    ``ingestion_failed`` event to it (``attempt = n``, with its own error text
    and payload hash). Nothing is updated: events are append-only, and
    ``submission`` has no counter column to bump, so the event list *is* the
    counter and every attempt's error is kept. The key is part of the lookup,
    not the payload, so a corrected payload retried under the same key lands on
    the same submission and is told apart by its ``payload_hash``.

    **Without a key nothing is deduplicated.** A payload hash would be safe to
    compute but not safe to act on: the server only hashes a body when a key
    was sent (so the failure path has no hash to use), and an unkeyed client
    has not declared that two requests are one attempt -- the same body can
    legitimately fail on Monday for a full artifact store and on Tuesday for a
    schema rule. Each unkeyed failure keeps its own row, as before.

    Returns the failed submission id (the existing one for a deduplicated
    retry), or ``None`` if recording itself failed.
    """
    if session_factory is None:
        # Lazy import keeps this service free of an app-layer import at module
        # load time.
        from app.api.deps import SessionLocal as session_factory  # type: ignore

    try:
        with session_factory() as session:
            with session.begin():
                existing: Optional[Submission] = None
                if retry_key is not None:
                    # Serialise concurrent failures of the same retry so two
                    # racing attempts cannot both open a submission. The lock
                    # is transaction-scoped and released at commit.
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                        {
                            "k": f"failed-upload:{created_by}:{retry_key.route}:"
                            f"{retry_key.idempotency_key}"
                        },
                    )
                    existing = _find_failed_submission(
                        session, created_by=created_by, kind=kind, key=retry_key
                    )

                details: Optional[dict[str, Any]] = None
                if retry_key is not None:
                    attempt = 1
                    if existing is not None:
                        attempt = (
                            session.scalar(
                                select(func.count())
                                .select_from(SubmissionAuditEvent)
                                .where(
                                    SubmissionAuditEvent.submission_id == existing.id,
                                    SubmissionAuditEvent.event_kind
                                    == SubmissionAuditEventKind.ingestion_failed,
                                )
                            )
                            or 0
                        ) + 1
                    details = {
                        "route": retry_key.route,
                        "idempotency_key": retry_key.idempotency_key,
                        "payload_hash": retry_key.payload_hash,
                        "attempt": attempt,
                    }

                if existing is not None:
                    submission = existing
                else:
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
                    details_json=details,
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
            retry_key = FailedUploadKey.from_context(kwargs.get("idem"))
            state: Optional[dict] = None
            if user is not None and isinstance(session, Session):
                state = {
                    "created_by": user.id,
                    "kind": kind,
                    "retry_key": retry_key,
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
                        retry_key=retry_key,
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
        retry_key=state.get("retry_key"),
    )


__all__ = [
    "SYNC_UPLOAD_AUDIT_KEY",
    "FailedUploadKey",
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
