"""Submission moderation ORM models.

This module defines the submission-centric moderation layer that sits
alongside — but intentionally separate from — the chemistry tables. One
submission represents one user contribution event. It may produce many
scientific records (linked via :class:`SubmissionRecordLink`) and carries
its own moderation status plus an append-only audit trail
(:class:`SubmissionAuditEvent`).

Design notes:

* ``supersedes_submission_id`` is stored on the *replacing* submission and
  points at the one it replaces. The inverse direction is exposed as a
  relationship, not a second column, to keep a single source of truth.
* ``SubmissionRecordLink`` uses a ``(record_type, record_id)`` pair rather
  than per-domain foreign keys so the index stays lightweight; the real
  foreign keys for the scientific records live on their own tables.
* Append-only behaviour for audit events is enforced at the service layer
  (no update/delete paths), matching the convention used by
  ``species_entry_review``.
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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionPrecheckLabel,
    SubmissionRecordType,
    SubmissionKind,
    SubmissionSourceKind,
    SubmissionStatus,
)

if TYPE_CHECKING:
    from app.db.models.app_user import AppUser


class Submission(Base, TimestampMixin, PublicRefMixin):
    """One user contribution event tracked for moderation and publication.

    The submission is the anchor for moderation state; the chemistry tables
    it creates remain scientifically focused and reference it only through
    :class:`SubmissionRecordLink`.
    """

    __tablename__ = "submission"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    submission_kind: Mapped[SubmissionKind] = mapped_column(
        SAEnum(SubmissionKind, name="submission_kind"),
        nullable=False,
    )
    source_kind: Mapped[SubmissionSourceKind] = mapped_column(
        SAEnum(SubmissionSourceKind, name="submission_source_kind"),
        nullable=False,
        default=SubmissionSourceKind.api,
        server_default=SubmissionSourceKind.api.value,
    )

    upload_job_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("upload_job.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    status: Mapped[SubmissionStatus] = mapped_column(
        SAEnum(SubmissionStatus, name="submission_status"),
        nullable=False,
        default=SubmissionStatus.pending,
        server_default=SubmissionStatus.pending.value,
    )

    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )

    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    approved_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    rejected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    rejected_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    correction_due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    supersedes_submission_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    llm_precheck_label: Mapped[Optional[SubmissionPrecheckLabel]] = mapped_column(
        SAEnum(SubmissionPrecheckLabel, name="submission_precheck_label"),
        nullable=True,
    )
    llm_precheck_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_precheck_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    llm_precheck_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    creator: Mapped["AppUser"] = relationship(
        foreign_keys=[created_by],
    )
    approver: Mapped[Optional["AppUser"]] = relationship(
        foreign_keys=[approved_by],
    )
    rejecter: Mapped[Optional["AppUser"]] = relationship(
        foreign_keys=[rejected_by],
    )

    supersedes: Mapped[Optional["Submission"]] = relationship(
        "Submission",
        remote_side="Submission.id",
        foreign_keys=[supersedes_submission_id],
        back_populates="superseded_by",
    )
    superseded_by: Mapped[list["Submission"]] = relationship(
        "Submission",
        foreign_keys=[supersedes_submission_id],
        back_populates="supersedes",
    )

    audit_events: Mapped[list["SubmissionAuditEvent"]] = relationship(
        back_populates="submission",
        cascade="save-update, merge",
        order_by="SubmissionAuditEvent.id",
        foreign_keys="SubmissionAuditEvent.submission_id",
    )
    record_links: Mapped[list["SubmissionRecordLink"]] = relationship(
        back_populates="submission",
        cascade="save-update, merge",
    )

    __table_args__ = (
        Index("ix_submission_status_created_at", "status", "created_at"),
        Index("ix_submission_created_by", "created_by"),
        Index("ix_submission_upload_job_id", "upload_job_id"),
        CheckConstraint(
            "(status <> 'rejected') OR (rejection_reason IS NOT NULL)",
            name="submission_rejected_requires_reason",
        ),
        CheckConstraint(
            "(status <> 'approved') OR (approved_by IS NOT NULL "
            "AND approved_by <> created_by)",
            name="submission_approver_not_creator",
        ),
        CheckConstraint(
            "(status <> 'rejected') OR (rejected_by IS NOT NULL "
            "AND rejected_by <> created_by)",
            name="submission_rejecter_not_creator",
        ),
    )

    @property
    def is_public(self) -> bool:
        """Derive public visibility from ``status``.

        ``approved`` is public; ``pending`` / ``precheck_passed`` content is
        considered visible-with-warning at the application layer but returns
        ``False`` here since it is not curator-approved. ``auto_flagged``,
        ``rejected``, and ``superseded`` are never public by default.
        """
        return self.status is SubmissionStatus.approved


class SubmissionAuditEvent(Base):
    """Append-only moderation/lifecycle event for a submission.

    Rows are written via :mod:`app.services.submission` and must never be
    updated or deleted through application code.
    """

    __tablename__ = "submission_audit_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )

    actor_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    actor_kind: Mapped[SubmissionActorKind] = mapped_column(
        SAEnum(SubmissionActorKind, name="submission_actor_kind"),
        nullable=False,
    )
    event_kind: Mapped[SubmissionAuditEventKind] = mapped_column(
        SAEnum(SubmissionAuditEventKind, name="submission_audit_event_kind"),
        nullable=False,
    )

    from_status: Mapped[Optional[SubmissionStatus]] = mapped_column(
        SAEnum(SubmissionStatus, name="submission_status"),
        nullable=True,
    )
    to_status: Mapped[Optional[SubmissionStatus]] = mapped_column(
        SAEnum(SubmissionStatus, name="submission_status"),
        nullable=True,
    )

    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    related_submission_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    submission: Mapped["Submission"] = relationship(
        back_populates="audit_events",
        foreign_keys=[submission_id],
    )
    actor: Mapped[Optional["AppUser"]] = relationship(foreign_keys=[actor_user_id])
    related_submission: Mapped[Optional["Submission"]] = relationship(
        foreign_keys=[related_submission_id],
    )

    __table_args__ = (
        Index("ix_submission_audit_event_submission_id", "submission_id"),
        Index("ix_submission_audit_event_event_kind", "event_kind"),
    )


class SubmissionRecordLink(Base, TimestampMixin):
    """Lightweight mapping from a submission to one scientific record it
    produced.

    Keeps moderation traceability out of the domain tables. Callers that
    need the real row should join through the ``record_type`` dispatch in
    application code — this table is a generic index, not a substitute for
    a typed foreign key.
    """

    __tablename__ = "submission_record_link"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    record_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    submission: Mapped["Submission"] = relationship(back_populates="record_links")

    __table_args__ = (
        Index(
            "ix_submission_record_link_submission_id",
            "submission_id",
        ),
        Index(
            "ix_submission_record_link_record",
            "record_type",
            "record_id",
        ),
        UniqueConstraint(
            "submission_id",
            "record_type",
            "record_id",
            "role",
            name="uq_submission_record_link_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
