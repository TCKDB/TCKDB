from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class IdempotencyRecord(Base, TimestampMixin):
    """Server-side retry-safety record for a successful keyed write.

    Scope is the tuple ``(user_id, request_method, endpoint, idempotency_key)``.
    The same key is allowed on different users, methods, or endpoints. The
    payload hash is the SHA-256 of the canonical JSON request body.
    Records expire 30 days after creation; expired rows are ignored for
    replay/conflict checks.
    """

    __tablename__ = "idempotency_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    request_method: Mapped[str] = mapped_column(String(8), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "request_method",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_record_user_method_endpoint_key",
        ),
    )
