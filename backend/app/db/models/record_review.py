"""Per-record review state ORM model.

``record_review`` carries the *consumer-facing* trust state of one
scientific record. It is intentionally orthogonal to:

* :class:`Submission` — moderation lifecycle of a contribution event,
* :class:`SubmissionRecordLink` — which submission produced which record,
* :class:`SpeciesEntryReview` — per-species attribution of who reviewed
  a species entry in what role (curator/reviewer/validator/linker).

There is exactly one current-state row per ``(record_type, record_id)``;
historical transitions are not persisted in this MVP. The
``SubmissionRecordType`` enum is reused as the record-type vocabulary so
the link table and review table share one shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import RecordReviewStatus, SubmissionRecordType

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

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    submission: Mapped[Optional["Submission"]] = relationship(
        foreign_keys=[submission_id],
    )
    reviewer: Mapped[Optional["AppUser"]] = relationship(
        foreign_keys=[reviewed_by],
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
    )
