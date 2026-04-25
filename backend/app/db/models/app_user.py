from __future__ import annotations

from typing import Optional

from sqlalchemy import CHAR, BigInteger, Boolean, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.models.common import AppUserRole


class AppUser(Base, TimestampMixin):
    """Application user identity used for curation provenance and auth."""

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    username: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    affiliation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    orcid: Mapped[Optional[str]] = mapped_column(CHAR(19), nullable=True)

    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    role: Mapped[AppUserRole] = mapped_column(
        SAEnum(AppUserRole, name="app_user_role"),
        nullable=False,
        default=AppUserRole.user,
        server_default=AppUserRole.user.value,
    )

    __table_args__ = (
        UniqueConstraint("username"),
        UniqueConstraint("email"),
        UniqueConstraint("orcid"),
    )
