"""Service helpers for the submission moderation lifecycle.

Everything that mutates a :class:`Submission` row (status changes,
precheck recording, record linkage, supersession) funnels through this
module so that:

* a matching :class:`SubmissionAuditEvent` is appended for every change,
* append-only behaviour for audit events is enforced in code (no
  update/delete paths exist), and
* business rules — curator/admin gating, rejection reason required, no
  uploader self-approval — live in one place.

The module deliberately does not handle authentication or authorisation
itself; the caller (API route or workflow) must pass the acting user and
this layer validates their :class:`AppUserRole` against the requested
action.

Current moderation is entirely curator-driven. The ``mark_precheck_result``
helper and the ``SubmissionActorKind.llm`` actor kind are reserved for a
possible future automated-review feature; they are not part of the MVP, no
HTTP route invokes them, and no background process is wired up. If/when
automated review lands, the only invariant baked into the rest of this
module is that an automated/LLM actor must never be recorded as a human
approver.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError, NotFoundError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionPrecheckLabel,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.services.record_review import (
    RecordRef,
    bulk_set_record_review_status,
)

if TYPE_CHECKING:
    from app.services.llm_precheck.schemas import LLMPrecheckResult
    from app.services.machine_review.schemas import MachineReviewProviderResultV2

_CURATION_ROLES = frozenset({AppUserRole.curator, AppUserRole.admin})


def _now_naive_utc() -> datetime:
    """Return a naive UTC timestamp suitable for ``DateTime(timezone=False)``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolve_actor_kind(user: AppUser) -> SubmissionActorKind:
    """Map an ``AppUser`` role onto the audit-event actor vocabulary.

    Users with the ``admin`` role record as :attr:`SubmissionActorKind.admin`,
    curators as :attr:`SubmissionActorKind.curator`, everyone else as
    :attr:`SubmissionActorKind.user`. LLM/system actor kinds are only
    produced through the dedicated precheck/system helpers below and never
    derive from an ``AppUser``.
    """
    if user.role is AppUserRole.admin:
        return SubmissionActorKind.admin
    if user.role is AppUserRole.curator:
        return SubmissionActorKind.curator
    return SubmissionActorKind.user


def _require_submission(session: Session, submission_id: int) -> Submission:
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise NotFoundError(f"Submission {submission_id} not found")
    return submission


def get_submission(session: Session, submission_id: int) -> Submission:
    """Return a submission by id or raise :class:`NotFoundError`.

    Public read-one helper for API routes; permission checks are the
    caller's responsibility.
    """
    return _require_submission(session, submission_id)


def _require_curator(user: AppUser) -> None:
    if user.role not in _CURATION_ROLES:
        raise DomainError("Curator or admin role required for this action")


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def create_submission(
    session: Session,
    *,
    created_by: int,
    submission_kind: SubmissionKind,
    source_kind: SubmissionSourceKind = SubmissionSourceKind.api,
    upload_job_id: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    supersedes_submission_id: int | None = None,
) -> Submission:
    """Create a pending submission and log ``submission_created``.

    :param created_by: Uploader's ``AppUser.id``.
    :param submission_kind: Submission family (``conformer``, ``thermo``, …).
    :param source_kind: Where the submission came from; defaults to ``api``.
    :param upload_job_id: Async upload-job id, if routed through the queue.
    :param supersedes_submission_id: Prior submission this one replaces. The
        previous submission is not mutated here; call :func:`supersede_submission`
        to flip its status once the new one is known to have ingested.
    :raises NotFoundError: If ``supersedes_submission_id`` is given but does
        not resolve to an existing submission.
    """
    if session.get(AppUser, created_by) is None:
        raise NotFoundError(f"AppUser {created_by} not found")

    if supersedes_submission_id is not None:
        _require_submission(session, supersedes_submission_id)

    submission = Submission(
        created_by=created_by,
        submission_kind=submission_kind,
        source_kind=source_kind,
        upload_job_id=upload_job_id,
        title=title,
        summary=summary,
        supersedes_submission_id=supersedes_submission_id,
        status=SubmissionStatus.pending,
    )
    session.add(submission)
    session.flush()

    append_audit_event(
        session,
        submission=submission,
        event_kind=SubmissionAuditEventKind.submission_created,
        actor_user_id=created_by,
        actor_kind=SubmissionActorKind.user,
        to_status=SubmissionStatus.pending,
        related_submission_id=supersedes_submission_id,
    )
    return submission


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def append_audit_event(
    session: Session,
    *,
    submission: Submission,
    event_kind: SubmissionAuditEventKind,
    actor_kind: SubmissionActorKind,
    actor_user_id: int | None = None,
    from_status: SubmissionStatus | None = None,
    to_status: SubmissionStatus | None = None,
    reason: str | None = None,
    summary: str | None = None,
    details_json: dict[str, Any] | None = None,
    related_submission_id: int | None = None,
) -> SubmissionAuditEvent:
    """Append one audit event. Never mutates prior events.

    Most callers should prefer the higher-level helpers (``approve_submission``,
    ``mark_precheck_result``, …) which use this internally; use this directly
    only for free-form events such as ``ingestion_succeeded`` from workflow
    code.
    """
    event = SubmissionAuditEvent(
        submission_id=submission.id,
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
        event_kind=event_kind,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        summary=summary,
        details_json=details_json,
        related_submission_id=related_submission_id,
    )
    session.add(event)
    session.flush()
    return event


def list_audit_events(
    session: Session, *, submission_id: int
) -> list[SubmissionAuditEvent]:
    """Return audit events for a submission, oldest first.

    :raises NotFoundError: If the submission does not exist, so callers can
        distinguish "no events yet" from "unknown submission".
    """
    _require_submission(session, submission_id)
    rows = session.scalars(
        select(SubmissionAuditEvent)
        .where(SubmissionAuditEvent.submission_id == submission_id)
        .order_by(SubmissionAuditEvent.id.asc())
    ).all()
    return list(rows)


def get_latest_llm_precheck_audit_event(
    session: Session, *, submission_id: int
) -> SubmissionAuditEvent | None:
    """Return the newest advisory LLM precheck audit event for a submission.

    Selection is deterministic: newest ``created_at`` wins, with the audit
    event primary key as a tie-breaker. Absence means only that no advisory
    review has been recorded.
    """
    _require_submission(session, submission_id)
    return session.scalars(
        select(SubmissionAuditEvent)
        .where(
            SubmissionAuditEvent.submission_id == submission_id,
            SubmissionAuditEvent.event_kind
            == SubmissionAuditEventKind.llm_precheck_recorded,
        )
        .order_by(
            SubmissionAuditEvent.created_at.desc(),
            SubmissionAuditEvent.id.desc(),
        )
        .limit(1)
    ).first()


# ---------------------------------------------------------------------------
# Ingestion outcome
# ---------------------------------------------------------------------------


def mark_ingestion_succeeded(
    session: Session,
    *,
    submission: Submission,
    summary: str | None = None,
    details_json: dict[str, Any] | None = None,
) -> SubmissionAuditEvent:
    """Log that chemistry ingestion produced the scientific records.

    The submission status is left unchanged — ingestion success does not
    imply moderation approval.
    """
    return append_audit_event(
        session,
        submission=submission,
        event_kind=SubmissionAuditEventKind.ingestion_succeeded,
        actor_kind=SubmissionActorKind.system,
        summary=summary,
        details_json=details_json,
    )


def mark_ingestion_failed(
    session: Session,
    *,
    submission: Submission,
    reason: str,
    details_json: dict[str, Any] | None = None,
) -> SubmissionAuditEvent:
    """Log that chemistry ingestion failed for this submission."""
    return append_audit_event(
        session,
        submission=submission,
        event_kind=SubmissionAuditEventKind.ingestion_failed,
        actor_kind=SubmissionActorKind.system,
        reason=reason,
        details_json=details_json,
    )


# ---------------------------------------------------------------------------
# Advisory LLM precheck audit
# ---------------------------------------------------------------------------


def record_llm_precheck_audit_event(
    session: Session,
    *,
    submission: Submission,
    result: "LLMPrecheckResult",
    provider: str | None = None,
    mode: str | None = None,
    error_kind: str | None = None,
) -> SubmissionAuditEvent:
    """Record an advisory LLM precheck result as an append-only audit event.

    This helper deliberately writes only ``submission_audit_event``. It does
    not change submission status, approval/rejection fields, precheck summary
    columns, record-review rows, or scientific records.
    """
    from app.services.llm_precheck.schemas import llm_precheck_result_to_details_json

    details_json = llm_precheck_result_to_details_json(result)
    if provider is not None:
        details_json["provider"] = provider
    if mode is not None:
        details_json["mode"] = mode
    if error_kind is not None:
        details_json["error_kind"] = error_kind

    return append_audit_event(
        session,
        submission=submission,
        event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
        actor_kind=SubmissionActorKind.llm,
        summary=result.summary,
        details_json=details_json,
    )


def record_machine_review_v2_audit_event(
    session: Session,
    *,
    submission: Submission,
    result: "MachineReviewProviderResultV2",
    provider: str | None = None,
) -> SubmissionAuditEvent:
    """Record a native v2 machine-review provider result as an audit event.

    Minimal service glue paralleling :func:`record_llm_precheck_audit_event`,
    but for the native v2 contract: the ``details_json`` carries
    ``schema_version="machine_review_v2"`` (the marker the adapter dispatches
    on), so the persisted event takes the adapter's v2 path on readback. Like
    the v1 helper it writes only ``submission_audit_event`` —
    ``actor_kind=llm``, ``event_kind=llm_precheck_recorded`` — and never mutates
    submission status, moderation, summary columns, record-review rows, or
    scientific records. It is **not** wired into the upload/precheck flow; it is
    an explicit, caller-driven recorder. Commit control stays with the caller
    (this only flushes, via :func:`append_audit_event`).
    """
    from app.services.machine_review.providers.interface import (
        machine_review_v2_result_to_details_json,
    )

    details_json = machine_review_v2_result_to_details_json(result)
    # The v2 result already carries ``provider`` as a first-class field; only
    # add a sibling key if an explicit override is supplied and the payload
    # didn't already set it (the adapter prefers the in-payload value).
    if provider is not None and details_json.get("provider") is None:
        details_json["provider"] = provider

    return append_audit_event(
        session,
        submission=submission,
        event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
        actor_kind=SubmissionActorKind.llm,
        summary=result.summary,
        details_json=details_json,
    )


# ---------------------------------------------------------------------------
# Precheck (reserved for future automated review — not part of the MVP)
# ---------------------------------------------------------------------------


def mark_precheck_result(
    session: Session,
    *,
    submission_id: int,
    label: SubmissionPrecheckLabel,
    model: str | None = None,
    summary: str | None = None,
    details_json: dict[str, Any] | None = None,
) -> Submission:
    """Record an automated precheck outcome on the submission.

    Reserved for a possible future automated-review feature. No HTTP route
    exposes this helper today and no background process invokes it; current
    moderation is curator-driven. The helper is kept (and tested) so the
    placeholder columns and audit-event vocabulary stay coherent if such a
    feature is added later.

    Only submissions still in the ``pending`` state advance to
    ``precheck_passed`` or ``auto_flagged``; precheck never overrides a
    curator decision and automated/LLM actors are never recorded as
    approvers.
    """
    submission = _require_submission(session, submission_id)

    if submission.status not in {SubmissionStatus.pending}:
        raise DomainError(
            "Precheck can only be applied to pending submissions "
            f"(current status: {submission.status.value})"
        )

    from_status = submission.status
    to_status = (
        SubmissionStatus.precheck_passed
        if label is SubmissionPrecheckLabel.passed
        else SubmissionStatus.auto_flagged
    )

    submission.status = to_status
    submission.llm_precheck_label = label
    submission.llm_precheck_model = model
    submission.llm_precheck_summary = summary
    submission.llm_precheck_at = _now_naive_utc()
    session.flush()

    event_kind = (
        SubmissionAuditEventKind.llm_precheck_passed
        if label is SubmissionPrecheckLabel.passed
        else SubmissionAuditEventKind.llm_precheck_flagged
    )
    append_audit_event(
        session,
        submission=submission,
        event_kind=event_kind,
        actor_kind=SubmissionActorKind.llm,
        from_status=from_status,
        to_status=to_status,
        summary=summary,
        details_json=details_json,
    )
    return submission


# ---------------------------------------------------------------------------
# Curator actions
# ---------------------------------------------------------------------------


_APPROVABLE_FROM = frozenset(
    {
        SubmissionStatus.pending,
        SubmissionStatus.precheck_passed,
        SubmissionStatus.auto_flagged,
    }
)


def _record_links_as_targets(
    submission: Submission,
) -> list[RecordRef]:
    """Project ``submission_record_link`` rows into the ``RecordRef`` shape
    used by the record-review service.
    """
    return [
        RecordRef(record_type=link.record_type, record_id=link.record_id)
        for link in submission.record_links
    ]


def approve_submission(
    session: Session,
    *,
    submission_id: int,
    actor: AppUser,
    summary: str | None = None,
) -> Submission:
    """Approve a submission and flip its linked records' review state.

    On approval, every ``submission_record_link`` target is moved to
    ``RecordReviewStatus.approved``. If the submission supersedes a
    prior one, the prior submission's linked records are *also* moved to
    ``RecordReviewStatus.deprecated`` here — superseding by itself does
    not deprecate (so a rejected correction never silently demotes the
    record it was meant to replace); approval of the replacement is
    when retirement takes effect.

    :raises DomainError: If the actor lacks curator/admin role, if the
        actor is also the uploader (self-approval is disallowed), or if
        the submission is not in an approvable state.
    """
    _require_curator(actor)
    submission = _require_submission(session, submission_id)

    if submission.created_by == actor.id:
        raise DomainError("Uploader cannot approve their own submission")

    if submission.status not in _APPROVABLE_FROM:
        raise DomainError(
            "Submission is not in an approvable state "
            f"(current status: {submission.status.value})"
        )

    from_status = submission.status
    submission.status = SubmissionStatus.approved
    submission.approved_by = actor.id
    submission.approved_at = _now_naive_utc()
    session.flush()

    bulk_set_record_review_status(
        session,
        targets=_record_links_as_targets(submission),
        status=RecordReviewStatus.approved,
        actor=actor,
        submission_id=submission.id,
    )

    if submission.supersedes_submission_id is not None:
        old = _require_submission(session, submission.supersedes_submission_id)
        bulk_set_record_review_status(
            session,
            targets=_record_links_as_targets(old),
            status=RecordReviewStatus.deprecated,
            actor=actor,
            submission_id=submission.id,
            note=(
                f"Deprecated by approval of superseding submission "
                f"#{submission.id}."
            ),
        )

    append_audit_event(
        session,
        submission=submission,
        event_kind=SubmissionAuditEventKind.curator_approved,
        actor_user_id=actor.id,
        actor_kind=_resolve_actor_kind(actor),
        from_status=from_status,
        to_status=SubmissionStatus.approved,
        summary=summary,
    )
    return submission


def reject_submission(
    session: Session,
    *,
    submission_id: int,
    actor: AppUser,
    reason: str,
    correction_due_at: datetime | None = None,
    summary: str | None = None,
) -> Submission:
    """Reject a submission. ``reason`` is required and recorded on the row.

    If ``correction_due_at`` is supplied, a ``correction_window_opened``
    event is appended alongside the rejection so the uploader's correction
    deadline is part of the audit trail.
    """
    _require_curator(actor)
    if not reason or not reason.strip():
        raise DomainError("Rejection reason is required")

    submission = _require_submission(session, submission_id)

    if submission.created_by == actor.id:
        raise DomainError("Uploader cannot reject their own submission")

    if submission.status in {SubmissionStatus.rejected, SubmissionStatus.superseded}:
        raise DomainError(
            "Submission is already in a terminal state "
            f"(current status: {submission.status.value})"
        )

    from_status = submission.status
    submission.status = SubmissionStatus.rejected
    submission.rejected_by = actor.id
    submission.rejected_at = _now_naive_utc()
    submission.rejection_reason = reason.strip()
    submission.correction_due_at = correction_due_at
    session.flush()

    bulk_set_record_review_status(
        session,
        targets=_record_links_as_targets(submission),
        status=RecordReviewStatus.rejected,
        actor=actor,
        submission_id=submission.id,
        note=reason.strip(),
    )

    append_audit_event(
        session,
        submission=submission,
        event_kind=SubmissionAuditEventKind.curator_rejected,
        actor_user_id=actor.id,
        actor_kind=_resolve_actor_kind(actor),
        from_status=from_status,
        to_status=SubmissionStatus.rejected,
        reason=reason.strip(),
        summary=summary,
    )
    if correction_due_at is not None:
        append_audit_event(
            session,
            submission=submission,
            event_kind=SubmissionAuditEventKind.correction_window_opened,
            actor_user_id=actor.id,
            actor_kind=_resolve_actor_kind(actor),
            details_json={"correction_due_at": correction_due_at.isoformat()},
        )
    return submission


def supersede_submission(
    session: Session,
    *,
    old_submission_id: int,
    new_submission_id: int,
    actor: AppUser | None = None,
) -> Submission:
    """Mark ``old`` as superseded by ``new`` and log the link.

    Superseding preserves the prior submission intact (no hard-delete) and
    records a ``submission_superseded`` event on both the old submission
    (as the primary target) and the new submission (via
    ``correction_uploaded``) so either end of the lineage exposes the
    relationship in its audit stream.

    Record-review state is *not* changed by supersede alone — the prior
    submission's ``approved`` records keep their ``approved`` review
    status until the replacing submission is itself approved, at which
    point :func:`approve_submission` deprecates them. This avoids
    silently hiding good data when a correction is uploaded but later
    rejected.

    ``old.supersedes_submission_id`` is not touched; the replacing
    submission's ``supersedes_submission_id`` is (and should already be) set
    to ``old_submission_id`` — we verify that here rather than mutate it.
    """
    if old_submission_id == new_submission_id:
        raise DomainError("A submission cannot supersede itself")

    old = _require_submission(session, old_submission_id)
    new = _require_submission(session, new_submission_id)

    if new.supersedes_submission_id != old_submission_id:
        raise DomainError(
            "Replacing submission must link back via supersedes_submission_id"
        )

    if old.status is SubmissionStatus.superseded:
        return old  # idempotent

    from_status = old.status
    old.status = SubmissionStatus.superseded
    session.flush()

    actor_user_id = actor.id if actor is not None else None
    actor_kind = (
        _resolve_actor_kind(actor) if actor is not None else SubmissionActorKind.system
    )

    append_audit_event(
        session,
        submission=old,
        event_kind=SubmissionAuditEventKind.submission_superseded,
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
        from_status=from_status,
        to_status=SubmissionStatus.superseded,
        related_submission_id=new_submission_id,
    )
    append_audit_event(
        session,
        submission=new,
        event_kind=SubmissionAuditEventKind.correction_uploaded,
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
        related_submission_id=old_submission_id,
    )
    return old


# ---------------------------------------------------------------------------
# Record linkage
# ---------------------------------------------------------------------------


def link_record(
    session: Session,
    *,
    submission: Submission,
    record_type: SubmissionRecordType,
    record_id: int,
    role: str | None = None,
) -> SubmissionRecordLink:
    """Attach one scientific record to the submission for traceability.

    Idempotent: if the exact ``(submission, record_type, record_id, role)``
    tuple already exists, the existing row is returned.
    """
    existing = session.scalar(
        select(SubmissionRecordLink).where(
            SubmissionRecordLink.submission_id == submission.id,
            SubmissionRecordLink.record_type == record_type,
            SubmissionRecordLink.record_id == record_id,
            SubmissionRecordLink.role.is_(role) if role is None else SubmissionRecordLink.role == role,
        )
    )
    if existing is not None:
        return existing

    link = SubmissionRecordLink(
        submission_id=submission.id,
        record_type=record_type,
        record_id=record_id,
        role=role,
    )
    session.add(link)
    session.flush()
    return link


def link_records(
    session: Session,
    *,
    submission: Submission,
    records: Iterable[tuple[SubmissionRecordType, int, Optional[str]]],
) -> list[SubmissionRecordLink]:
    """Bulk helper that calls :func:`link_record` for each ``(type, id, role)``.

    Duplicates within ``records`` are collapsed by the per-row idempotency.
    """
    return [
        link_record(
            session,
            submission=submission,
            record_type=record_type,
            record_id=record_id,
            role=role,
        )
        for record_type, record_id, role in records
    ]


def list_record_links(
    session: Session, *, submission_id: int
) -> list[SubmissionRecordLink]:
    """Return all record links for a submission, oldest first."""
    _require_submission(session, submission_id)
    rows = session.scalars(
        select(SubmissionRecordLink)
        .where(SubmissionRecordLink.submission_id == submission_id)
        .order_by(SubmissionRecordLink.id.asc())
    ).all()
    return list(rows)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_my_submissions(
    session: Session,
    *,
    user_id: int,
    statuses: Iterable[SubmissionStatus] | None = None,
) -> list[Submission]:
    """Return submissions created by ``user_id``, newest first."""
    stmt = select(Submission).where(Submission.created_by == user_id)
    if statuses is not None:
        stmt = stmt.where(Submission.status.in_(list(statuses)))
    stmt = stmt.order_by(Submission.created_at.desc(), Submission.id.desc())
    return list(session.scalars(stmt).all())


def list_submissions_for_review(
    session: Session,
    *,
    statuses: Iterable[SubmissionStatus] | None = None,
) -> list[Submission]:
    """Return submissions awaiting curator review, oldest first.

    Default scope is ``pending`` / ``precheck_passed`` / ``auto_flagged`` —
    the three pre-approval states a curator acts on.
    """
    scoped = list(statuses) if statuses is not None else [
        SubmissionStatus.pending,
        SubmissionStatus.precheck_passed,
        SubmissionStatus.auto_flagged,
    ]
    stmt = (
        select(Submission)
        .where(Submission.status.in_(scoped))
        .order_by(Submission.created_at.asc(), Submission.id.asc())
    )
    return list(session.scalars(stmt).all())
