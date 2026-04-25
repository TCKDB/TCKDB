from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import CHAR, BigInteger, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.literature_author import LiteratureAuthor


class Author(Base, TimestampMixin):
    __tablename__ = "author"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    given_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    family_name: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    orcid: Mapped[Optional[str]] = mapped_column(CHAR(19), nullable=True)

    literature_links: Mapped[list["LiteratureAuthor"]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",
    )

    __table_args__ = (UniqueConstraint("orcid"),)
