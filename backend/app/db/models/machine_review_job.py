"""Machine-review job queue, progress log and admin switches.

``machine_review_job`` is the lifecycle row for one machine-review attempt on a
submission; the review itself stays in ``submission_audit_event``.
``machine_review_job_event`` is its append-only progress log, and
``machine_review_settings`` is the single row of operator switches.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.common import (
    MachineReviewExecutor,
    MachineReviewJobEventKind,
    MachineReviewJobFailure,
    MachineReviewJobStatus,
    MachineReviewProviderKey,
)


class MachineReviewJob(Base):
    """One machine-review attempt on a submission: who asked, which backend, how it ended.

    This is provenance of an attempt, not the review. At most one job per
    submission is open (queued or running) at a time; the lifecycle columns are
    the only ones that change after insert.
    """

    __tablename__ = "machine_review_job"
    __table_args__ = (
        CheckConstraint(
            "(status = 'succeeded' AND audit_event_id IS NOT NULL) "
            "OR (status IN ('queued', 'running', 'cancelled') AND audit_event_id IS NULL) "
            "OR status = 'failed'",
            name="audit_event_state",
        ),
        CheckConstraint(
            "(status = 'failed' AND failure_code IS NOT NULL) "
            "OR (status = 'cancelled' AND (failure_code IS NULL OR failure_code = 'machine_review_disabled')) "
            "OR (status IN ('queued', 'running', 'succeeded') AND failure_code IS NULL)",
            name="failure_code_state",
        ),
        CheckConstraint("char_length(failure_detail) <= 2000", name="failure_detail_length"),
        Index(
            "ux_machine_review_job_open_submission",
            "submission_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_machine_review_job_status_created_at", "status", "created_at"),
        Index("ix_machine_review_job_submission_id_created_at", "submission_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    requested_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    provider_key: Mapped[MachineReviewProviderKey] = mapped_column(
        SAEnum(MachineReviewProviderKey, name="machine_review_provider_key"), nullable=False
    )
    executor: Mapped[MachineReviewExecutor] = mapped_column(
        SAEnum(MachineReviewExecutor, name="machine_review_executor"), nullable=False
    )
    status: Mapped[MachineReviewJobStatus] = mapped_column(
        SAEnum(MachineReviewJobStatus, name="machine_review_job_status"),
        server_default=MachineReviewJobStatus.queued.value,
        nullable=False,
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    failure_code: Mapped[MachineReviewJobFailure | None] = mapped_column(
        SAEnum(MachineReviewJobFailure, name="machine_review_job_failure"), nullable=True
    )
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    context_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runner_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="2", nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    audit_event_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("submission_audit_event.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MachineReviewJobEvent(Base):
    """One progress entry of a machine-review job, numbered in order.

    Provenance of how an attempt ran. ``detail_json`` is the admin-only
    transcript payload and is written only when full-context storage is on.
    """

    __tablename__ = "machine_review_job_event"
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_machine_review_job_event_job_id_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("machine_review_job.id", ondelete="CASCADE", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[MachineReviewJobEventKind] = mapped_column(
        SAEnum(MachineReviewJobEventKind, name="machine_review_job_event_kind"), nullable=False
    )
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class MachineReviewSettings(Base):
    """The single row of admin switches for machine review.

    An absent row means both switches are on. The deployment setting
    ``AI_REVIEW_ASSISTANT_MODE=off`` overrides these.
    """

    __tablename__ = "machine_review_settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    machine_review_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    curator_requests_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
