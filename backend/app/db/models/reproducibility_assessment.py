"""Append-only reproducibility assessments for scientific records.

Each row is a versioned assessor claim about the evidence available for one
``(record_type, record_id)`` at one instant.  New evidence or a new rubric
appends another row; no row is updated in place.  The database migration adds
a trigger that rejects ``UPDATE`` and ``DELETE`` independently of application
code. The stored grade is not an independently verified guarantee that the
record meets the rubric.

This curation projection is deliberately separate from both human
``record_review`` status and deterministic trust/evidence badges.  A record can
be curator-approved yet incompletely reproducible, or richly reproducible
without being curator-approved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.models.common import (
    ReproducibilityAssessorKind,
    ReproducibilityGrade,
    SubmissionRecordType,
)


class RecordReproducibilityAssessment(Base, TimestampMixin):
    """One immutable, versioned assessor claim about reproducibility."""

    __tablename__ = "record_reproducibility_assessment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    record_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    grade: Mapped[ReproducibilityGrade] = mapped_column(
        SAEnum(ReproducibilityGrade, name="reproducibility_grade"),
        nullable=False,
    )
    rubric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(64), nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    passed_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    missing_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    warnings_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    assessor_kind: Mapped[ReproducibilityAssessorKind] = mapped_column(
        SAEnum(
            ReproducibilityAssessorKind,
            name="reproducibility_assessor_kind",
        ),
        nullable=False,
    )
    assessor_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "app_user.id",
            name="fk_repro_assessment_assessor_user",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    source_submission_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "submission.id",
            name="fk_repro_assessment_source_submission",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )

    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_repro_assessment_record_latest",
            "record_type",
            "record_id",
            text("assessed_at DESC"),
            text("id DESC"),
        ),
        Index("ix_repro_assessment_context_hash", "context_hash"),
        Index("ix_repro_assessment_assessor_user_id", "assessor_user_id"),
        Index("ix_repro_assessment_source_submission_id", "source_submission_id"),
        CheckConstraint(
            "context_hash ~ '^[0-9a-f]{64}$'",
            name="context_hash_lower_hex",
        ),
        CheckConstraint(
            "jsonb_typeof(context_json) = 'object'",
            name="context_json_is_object",
        ),
        CheckConstraint(
            "jsonb_typeof(passed_json) = 'array'",
            name="passed_json_is_array",
        ),
        CheckConstraint(
            "jsonb_typeof(missing_json) = 'array'",
            name="missing_json_is_array",
        ),
        CheckConstraint(
            "jsonb_typeof(warnings_json) = 'array'",
            name="warnings_json_is_array",
        ),
        CheckConstraint(
            "(assessor_kind = 'system' AND assessor_user_id IS NULL) OR "
            "(assessor_kind = 'curator' AND assessor_user_id IS NOT NULL)",
            name="assessor_user_consistent",
        ),
    )
