from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# https://alembic.sqlalchemy.org/en/latest/naming.html
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )


class CreatedByMixin:
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"), nullable=True
    )


class PublicRefMixin:
    """Phase A public-ref column for ref-bearing tables.

    Auto-populated at INSERT time by a global SQLAlchemy ``before_insert``
    listener installed by
    :func:`app.services.public_refs.install_public_ref_listener`. Each
    ref-bearing ORM model adds ``PublicRefMixin`` to its inheritance.
    See ``docs/specs/public_identifier_policy.md``.

    Format: ``{prefix}_{26-char base32 lowercase}`` (≤31 chars). Stored
    as ``String(40)`` for headroom; ``UNIQUE`` and ``NOT NULL``.
    """

    public_ref: Mapped[str] = mapped_column(
        String(40),
        unique=True,
        nullable=False,
        index=True,
    )
