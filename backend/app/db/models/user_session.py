from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CHAR, BigInteger, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class UserSession(Base, TimestampMixin):
    """Server-side session record for human browser authentication.

    The cookie-borne token value is never stored — only a SHA-256 hash.
    Sessions are invalidated by setting ``revoked_at`` or by passing
    ``expires_at``; both conditions short-circuit :func:`get_current_user`.
    """

    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    __table_args__ = (UniqueConstraint("token_hash"),)
