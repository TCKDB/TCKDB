"""Service helpers for the per-record review/trust state.

``record_review`` carries the consumer-facing trust state of one
scientific record. It is orthogonal to:

* ``submission.status`` — moderation lifecycle of a contribution event,
* ``submission_record_link`` — index from a submission to its records,
* ``species_entry_review`` — per-species attribution of who reviewed in
  what role.

All mutations funnel through this module so the transition policy and
self-approval guard live in one place.

Transition policy (set in :data:`_ALLOWED_TRANSITIONS`):

* ``not_reviewed`` → ``under_review``, ``approved``, ``rejected``, ``deprecated``
* ``under_review`` → ``approved``, ``rejected``, ``not_reviewed``
* ``approved``    → ``under_review``, ``deprecated``
* ``rejected``    → ``under_review``, ``deprecated``
* ``deprecated``  → ``under_review``, ``approved``

Disallowed by default — these intentionally route through
``under_review`` so a re-review is recorded:

* ``approved`` → ``rejected``, ``rejected`` → ``approved``,
  ``deprecated`` → ``rejected``.

Self-approval guard: an actor whose ``id`` equals the row's
``created_by`` cannot transition the row to ``approved``. Other
transitions a creator can perform if they have the curator/admin role.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview
from app.db.models.submission import SubmissionRecordLink

_CURATION_ROLES = frozenset({AppUserRole.curator, AppUserRole.admin})

_TERMINAL_STATUSES = frozenset(
    {
        RecordReviewStatus.approved,
        RecordReviewStatus.rejected,
        RecordReviewStatus.deprecated,
    }
)

_ALLOWED_TRANSITIONS: dict[RecordReviewStatus, frozenset[RecordReviewStatus]] = {
    RecordReviewStatus.not_reviewed: frozenset(
        {
            RecordReviewStatus.under_review,
            RecordReviewStatus.approved,
            RecordReviewStatus.rejected,
            RecordReviewStatus.deprecated,
        }
    ),
    RecordReviewStatus.under_review: frozenset(
        {
            RecordReviewStatus.approved,
            RecordReviewStatus.rejected,
            RecordReviewStatus.not_reviewed,
        }
    ),
    RecordReviewStatus.approved: frozenset(
        {
            RecordReviewStatus.under_review,
            RecordReviewStatus.deprecated,
        }
    ),
    RecordReviewStatus.rejected: frozenset(
        {
            RecordReviewStatus.under_review,
            RecordReviewStatus.deprecated,
        }
    ),
    RecordReviewStatus.deprecated: frozenset(
        {
            RecordReviewStatus.under_review,
            RecordReviewStatus.approved,
        }
    ),
}


@dataclass(frozen=True)
class RecordRef:
    """``(record_type, record_id)`` pair, the natural key for review rows."""

    record_type: SubmissionRecordType
    record_id: int


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _require_curator(actor: AppUser) -> None:
    if actor.role not in _CURATION_ROLES:
        raise DomainError("Curator or admin role required for this action")


def _check_transition_allowed(
    *,
    from_status: RecordReviewStatus,
    to_status: RecordReviewStatus,
) -> None:
    """Raise :class:`DomainError` if the requested transition is disallowed.

    Same-status transitions (``X → X``) are silently allowed: they are
    no-ops and the caller may rely on idempotent behaviour.
    """
    if from_status == to_status:
        return
    allowed = _ALLOWED_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise DomainError(
            f"Disallowed record-review transition: {from_status.value} → "
            f"{to_status.value} (route through 'under_review' instead)."
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_record_review(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
) -> Optional[RecordReview]:
    """Return the current review row for ``(record_type, record_id)`` or ``None``.

    Callers that want to treat "no row" as ``not_reviewed`` should do so
    explicitly at the call site.
    """
    return session.scalar(
        select(RecordReview).where(
            RecordReview.record_type == record_type,
            RecordReview.record_id == record_id,
        )
    )


def list_record_reviews(
    session: Session,
    *,
    record_type: Optional[SubmissionRecordType] = None,
    status: Optional[RecordReviewStatus] = None,
    submission_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[RecordReview]:
    """Return review rows newest-first, optionally filtered."""
    stmt = select(RecordReview)
    if record_type is not None:
        stmt = stmt.where(RecordReview.record_type == record_type)
    if status is not None:
        stmt = stmt.where(RecordReview.status == status)
    if submission_id is not None:
        stmt = stmt.where(RecordReview.submission_id == submission_id)
    stmt = (
        stmt.order_by(RecordReview.created_at.desc(), RecordReview.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def ensure_record_review(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
    status: RecordReviewStatus = RecordReviewStatus.not_reviewed,
    submission_id: Optional[int] = None,
    created_by: Optional[int] = None,
    note: Optional[str] = None,
) -> RecordReview:
    """Idempotently ensure a review row exists for ``(record_type, record_id)``.

    First call inserts. Subsequent calls return the existing row unchanged
    — this helper does not mutate ``status`` once written. Use
    :func:`set_record_review_status` (or ``bulk_*``) for status changes.

    The default ``status=not_reviewed`` is for internal/nested callers that
    create records outside a contribution event. Both ingestion pathways —
    direct ``/uploads/*`` and hosted ``/bundles/submit`` — pass
    ``status=under_review`` and a ``submission_id`` explicitly (see
    :class:`ReviewPolicy`). Callers must never rely on this helper inferring
    moderation context.
    """
    if status in _TERMINAL_STATUSES:
        # Terminal statuses require an explicit reviewer, so they can't be
        # ensured-on-create through this helper.
        raise DomainError(
            "ensure_record_review cannot create rows in a terminal status; "
            "use set_record_review_status for approved/rejected/deprecated."
        )

    existing = get_record_review(
        session, record_type=record_type, record_id=record_id
    )
    if existing is not None:
        return existing

    review = RecordReview(
        record_type=record_type,
        record_id=record_id,
        status=status,
        submission_id=submission_id,
        created_by=created_by,
        note=note,
    )
    session.add(review)
    session.flush()
    return review


def set_record_review_status(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
    status: RecordReviewStatus,
    actor: AppUser,
    submission_id: Optional[int] = None,
    note: Optional[str] = None,
) -> RecordReview:
    """Curator/admin-gated transition of one record's review status.

    Creates the row on first touch (via :func:`ensure_record_review`
    semantics for non-terminal targets, or directly here for terminal
    targets) so that an initial direct-to-approved/rejected/deprecated
    write is supported when the actor has the authority.

    Self-approval guard: ``actor.id`` may not equal the existing row's
    ``created_by`` when transitioning to :attr:`RecordReviewStatus.approved`.
    Other transitions are allowed for the creator if they meet the role
    check — self-deprecation and self-reopening are different trust
    problems than self-approval and are not blocked here.
    """
    _require_curator(actor)

    existing = get_record_review(
        session, record_type=record_type, record_id=record_id
    )
    if existing is None:
        # Bootstrap row at not_reviewed and then transition, so the
        # transition policy and reviewer-stamp logic below run uniformly.
        existing = RecordReview(
            record_type=record_type,
            record_id=record_id,
            status=RecordReviewStatus.not_reviewed,
            submission_id=submission_id,
            created_by=None,
            note=None,
        )
        session.add(existing)
        session.flush()

    _check_transition_allowed(
        from_status=existing.status,
        to_status=status,
    )

    if status is RecordReviewStatus.approved:
        if existing.created_by is not None and existing.created_by == actor.id:
            raise DomainError(
                "Actor cannot approve a record they created."
            )

    existing.status = status
    if submission_id is not None:
        existing.submission_id = submission_id
    if note is not None:
        existing.note = note

    if status in _TERMINAL_STATUSES:
        existing.reviewed_by = actor.id
        existing.reviewed_at = _now_naive_utc()
    else:
        # not_reviewed / under_review: reviewer metadata is meaningless;
        # null it for clear semantics.
        existing.reviewed_by = None
        existing.reviewed_at = None

    session.flush()
    return existing


def bulk_set_record_review_status(
    session: Session,
    *,
    targets: Iterable[RecordRef],
    status: RecordReviewStatus,
    actor: AppUser,
    submission_id: Optional[int] = None,
    note: Optional[str] = None,
) -> list[RecordReview]:
    """Apply :func:`set_record_review_status` across many records.

    Used by the submission lifecycle to flip every linked record to
    ``approved`` / ``rejected`` / ``deprecated`` in one go. The whole
    batch shares the same actor and timestamp; if any single transition
    is disallowed, the call raises before mutating later targets and
    the caller's outer transaction rolls back.
    """
    out: list[RecordReview] = []
    for target in targets:
        out.append(
            set_record_review_status(
                session,
                record_type=target.record_type,
                record_id=target.record_id,
                status=status,
                actor=actor,
                submission_id=submission_id,
                note=note,
            )
        )
    return out


def bulk_ensure_record_reviews(
    session: Session,
    *,
    targets: Iterable[RecordRef],
    status: RecordReviewStatus = RecordReviewStatus.not_reviewed,
    submission_id: Optional[int] = None,
    created_by: Optional[int] = None,
    note: Optional[str] = None,
) -> list[RecordReview]:
    """Idempotently ensure review rows exist for many records."""
    out: list[RecordReview] = []
    for target in targets:
        out.append(
            ensure_record_review(
                session,
                record_type=target.record_type,
                record_id=target.record_id,
                status=status,
                submission_id=submission_id,
                created_by=created_by,
                note=note,
            )
        )
    return out


def get_reviews_for_records(
    session: Session,
    *,
    targets: Iterable[RecordRef],
) -> dict[tuple[SubmissionRecordType, int], RecordReview]:
    """Bulk fetch existing review rows keyed by ``(record_type, record_id)``."""
    pairs = list(targets)
    if not pairs:
        return {}
    stmt = select(RecordReview).where(
        or_(
            *(
                and_(
                    RecordReview.record_type == p.record_type,
                    RecordReview.record_id == p.record_id,
                )
                for p in pairs
            )
        )
    )
    return {
        (row.record_type, row.record_id): row
        for row in session.scalars(stmt).all()
    }


@dataclass(frozen=True)
class ReviewPolicy:
    """How a workflow should record review rows for the entities it creates.

    Workflows accept a ``ReviewPolicy`` rather than inferring moderation
    context from their environment; the route or higher-level orchestrator
    sets it explicitly:

    * legacy direct ingest → ``ReviewPolicy()`` (default, ``not_reviewed``,
      no submission link) — kept for nested/internal callers and tests,
    * direct ``/uploads/*`` ingest → ``ReviewPolicy(status=under_review,
      submission_id=submission.id, link_records=True)``: every produced
      record is initialised under review *and* linked to the submission,
    * hosted bundle submission → ``ReviewPolicy(status=under_review,
      submission_id=submission.id)``: the bundle workflow creates its own
      curated ``submission_record_link`` rows, so it leaves
      ``link_records`` False to avoid linking the full target set twice.

    ``link_records`` only takes effect when ``submission_id`` is set.
    """

    status: RecordReviewStatus = RecordReviewStatus.not_reviewed
    submission_id: Optional[int] = None
    link_records: bool = False


_DEFAULT_REVIEW_POLICY = ReviewPolicy()


def _ensure_record_link(
    session: Session,
    *,
    submission_id: int,
    record_type: SubmissionRecordType,
    record_id: int,
) -> None:
    """Idempotently attach ``(record_type, record_id)`` to a submission.

    Mirrors :func:`app.services.submission.link_record` but writes the model
    row directly so this module stays free of a service-level import cycle
    (``app.services.submission`` imports from here). Links are created with
    ``role=None``; the curated, role-bearing links the bundle workflow makes
    remain its own responsibility.
    """
    existing = session.scalar(
        select(SubmissionRecordLink).where(
            SubmissionRecordLink.submission_id == submission_id,
            SubmissionRecordLink.record_type == record_type,
            SubmissionRecordLink.record_id == record_id,
            SubmissionRecordLink.role.is_(None),
        )
    )
    if existing is not None:
        return
    session.add(
        SubmissionRecordLink(
            submission_id=submission_id,
            record_type=record_type,
            record_id=record_id,
            role=None,
        )
    )
    session.flush()


def apply_review_policy(
    session: Session,
    *,
    targets: Iterable[RecordRef],
    policy: Optional[ReviewPolicy],
    created_by: Optional[int],
) -> list[RecordReview]:
    """Workflow-side entry point for ensuring review rows post-persist.

    Idempotent — existing review rows are returned unchanged. Pass
    ``policy=None`` to opt out (used by callers that only want to recurse
    into nested workflows without double-writing).

    When the policy carries a ``submission_id`` and ``link_records`` is set,
    each target is also idempotently linked to that submission, so the upload
    event's full record set is traceable through ``submission_record_link``.
    """
    if policy is None:
        return []
    targets = list(targets)
    reviews = bulk_ensure_record_reviews(
        session,
        targets=targets,
        status=policy.status,
        submission_id=policy.submission_id,
        created_by=created_by,
    )
    if policy.submission_id is not None and policy.link_records:
        for target in targets:
            _ensure_record_link(
                session,
                submission_id=policy.submission_id,
                record_type=target.record_type,
                record_id=target.record_id,
            )
    return reviews


__all__ = [
    "RecordRef",
    "ReviewPolicy",
    "apply_review_policy",
    "ensure_record_review",
    "set_record_review_status",
    "bulk_set_record_review_status",
    "bulk_ensure_record_reviews",
    "get_record_review",
    "get_reviews_for_records",
    "list_record_reviews",
]
