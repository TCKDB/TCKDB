"""Curator-written lessons for the machine reviewer.

``machine_review_lesson`` is a curation overlay: a short note from a curator or
admin about what the machine reviewer got wrong. Only a ``verified`` lesson may
reach a review; the model never writes one. No scientific value is stored here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.models.common import (
    MachineReviewCategory,
    MachineReviewLessonSignal,
    MachineReviewLessonStatus,
    SubmissionRecordType,
)

#: Longest lesson text, in characters.
LESSON_TEXT_MAX_CHARS = 500


def _fk(target: str, name: str, *, ondelete: str | None = None) -> ForeignKey:
    return ForeignKey(target, name=name, ondelete=ondelete, deferrable=True, initially="IMMEDIATE")


class MachineReviewLesson(Base, TimestampMixin):
    """One curator-written lesson and where it stands: proposed, verified or retired (curation)."""

    __tablename__ = "machine_review_lesson"
    __table_args__ = (
        CheckConstraint(
            f"char_length(btrim(text, E' \\t\\r\\n')) BETWEEN 1 AND {LESSON_TEXT_MAX_CHARS}", name="text_length"
        ),
        CheckConstraint(
            "(status = 'proposed' AND verified_by IS NULL AND verified_at IS NULL "
            "AND retired_by IS NULL AND retired_at IS NULL) "
            "OR (status = 'verified' AND verified_by IS NOT NULL AND verified_at IS NOT NULL "
            "AND retired_by IS NULL AND retired_at IS NULL) "
            "OR (status = 'retired' AND retired_by IS NOT NULL AND retired_at IS NOT NULL)",
            name="status_stamps",
        ),
        Index("ix_machine_review_lesson_status_verified_at", "status", "verified_at"),
        Index("ix_machine_review_lesson_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    category: Mapped[MachineReviewCategory | None] = mapped_column(
        SAEnum(MachineReviewCategory, name="machine_review_category"), nullable=True
    )
    record_type: Mapped[SubmissionRecordType | None] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"), nullable=True
    )
    scope_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    signal: Mapped[MachineReviewLessonSignal] = mapped_column(
        SAEnum(MachineReviewLessonSignal, name="machine_review_lesson_signal"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[MachineReviewLessonStatus] = mapped_column(
        SAEnum(MachineReviewLessonStatus, name="machine_review_lesson_status"),
        server_default=MachineReviewLessonStatus.proposed.value,
        nullable=False,
    )
    source_job_id: Mapped[int | None] = mapped_column(
        BigInteger, _fk("machine_review_job.id", "fk_mr_lesson_source_job", ondelete="SET NULL"), nullable=True
    )
    source_curator_task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        _fk("machine_review_curator_task.id", "fk_mr_lesson_source_task", ondelete="SET NULL"),
        nullable=True,
    )
    created_by: Mapped[int] = mapped_column(
        BigInteger, _fk("app_user.id", "fk_mr_lesson_created_by"), nullable=False
    )
    verified_by: Mapped[int | None] = mapped_column(
        BigInteger, _fk("app_user.id", "fk_mr_lesson_verified_by"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    retired_by: Mapped[int | None] = mapped_column(
        BigInteger, _fk("app_user.id", "fk_mr_lesson_retired_by"), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
