from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.common import UploadJobKind, UploadJobStatus


class UploadJob(Base):
    """Async upload job queue backed by PostgreSQL.

    Each row represents one pending, in-flight, or completed upload.
    Workers claim rows using ``SELECT … FOR UPDATE SKIP LOCKED`` so
    multiple workers can run safely without double-processing.
    """

    __tablename__ = "upload_job"
    __table_args__ = (
        Index("ix_upload_job_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    status: Mapped[UploadJobStatus] = mapped_column(
        SAEnum(UploadJobStatus, name="upload_job_status"),
        server_default=UploadJobStatus.queued.value,
        nullable=False,
    )
    kind: Mapped[UploadJobKind] = mapped_column(
        SAEnum(UploadJobKind, name="upload_job_kind"),
        nullable=False,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="3", nullable=False)
