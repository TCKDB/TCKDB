from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CHAR, BigInteger, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ApiKey(Base, TimestampMixin):
    """API keys owned by an :class:`AppUser`.

    The plain key value is never stored — only a SHA-256 hash of the
    ``tck_<random>`` token issued at creation time.  Keys are revoked
    by setting ``revoked_at``; revocation takes effect on the next
    request because lookups filter on the null-revoked case.
    """

    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    key_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    __table_args__ = (UniqueConstraint("key_hash"),)
