"""Per-record review state ORM model.

``record_review`` carries the *consumer-facing* trust state of one
scientific record. It is intentionally orthogonal to:

* :class:`Submission` — moderation lifecycle of a contribution event,
* :class:`SubmissionRecordLink` — which submission produced which record,
* :class:`SpeciesEntryReview` — per-species attribution of who reviewed
  a species entry in what role (curator/reviewer/validator/linker).

There is exactly one current-state row per ``(record_type, record_id)``;
the append-only :class:`RecordReviewEvent` log preserves the history of
who changed the status when (mirroring
:class:`app.db.models.submission.SubmissionAuditEvent`). The
``SubmissionRecordType`` enum is reused as the record-type vocabulary so
the link table and review table share one shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import (
    RecordReviewEventKind,
    RecordReviewStatus,
    SubmissionRecordType,
)

if TYPE_CHECKING:
    from app.db.models.app_user import AppUser
    from app.db.models.submission import Submission


class RecordReview(Base, TimestampMixin, CreatedByMixin):
    """Current review/trust state for one scientific record."""

    __tablename__ = "record_review"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    record_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[RecordReviewStatus] = mapped_column(
        SAEnum(RecordReviewStatus, name="record_review_status"),
        nullable=False,
        default=RecordReviewStatus.not_reviewed,
        server_default=RecordReviewStatus.not_reviewed.value,
    )

    submission_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    reviewed_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    first_approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
        doc="Permanent timestamp of this record's first approval.",
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    submission: Mapped[Optional["Submission"]] = relationship(
        foreign_keys=[submission_id],
    )
    reviewer: Mapped[Optional["AppUser"]] = relationship(
        foreign_keys=[reviewed_by],
    )

    events: Mapped[list["RecordReviewEvent"]] = relationship(
        back_populates="record_review",
        order_by="RecordReviewEvent.id",
        # Append-only history: no delete-orphan. Mirrors SubmissionAuditEvent,
        # which deliberately keeps the ORM from ever deleting audit rows.
        cascade="save-update, merge",
    )

    __table_args__ = (
        UniqueConstraint(
            "record_type",
            "record_id",
            name="uq_record_review_record",
        ),
        Index(
            "ix_record_review_status_record_type",
            "status",
            "record_type",
        ),
        Index("ix_record_review_submission_id", "submission_id"),
        CheckConstraint(
            "(status NOT IN ('approved', 'rejected', 'deprecated')) "
            "OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="record_review_terminal_requires_reviewer",
        ),
        CheckConstraint(
            "status <> 'approved' OR first_approved_at IS NOT NULL",
            name="record_review_approved_has_first_approval",
        ),
    )


class RecordReviewEvent(Base):
    """Append-only history event for one :class:`RecordReview` row.

    Preserves who-changed-what-when for consumer-facing review state.
    Rows are written via :mod:`app.services.record_review` and must never be
    updated or deleted through application code — mirrors the append-only
    contract of :class:`app.db.models.submission.SubmissionAuditEvent`.
    """

    __tablename__ = "record_review_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    record_review_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("record_review.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    event_kind: Mapped[RecordReviewEventKind] = mapped_column(
        SAEnum(RecordReviewEventKind, name="record_review_event_kind"),
        nullable=False,
    )

    from_status: Mapped[Optional[RecordReviewStatus]] = mapped_column(
        SAEnum(RecordReviewStatus, name="record_review_status"),
        nullable=True,
    )
    to_status: Mapped[Optional[RecordReviewStatus]] = mapped_column(
        SAEnum(RecordReviewStatus, name="record_review_status"),
        nullable=True,
    )

    actor_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )

    record_review: Mapped["RecordReview"] = relationship(
        back_populates="events",
        foreign_keys=[record_review_id],
    )
    actor: Mapped[Optional["AppUser"]] = relationship(
        foreign_keys=[actor_user_id],
    )

    __table_args__ = (
        Index("ix_record_review_event_record_review_id", "record_review_id"),
    )
