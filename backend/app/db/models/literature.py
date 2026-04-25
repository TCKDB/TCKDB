from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Index, Integer, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.models.common import LiteratureKind

if TYPE_CHECKING:
    from app.db.models.literature_author import LiteratureAuthor


class Literature(Base, TimestampMixin):
    __tablename__ = "literature"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    kind: Mapped[LiteratureKind] = mapped_column(
        SAEnum(LiteratureKind, name="literature_kind"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)

    journal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    volume: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    issue: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pages: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    doi: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    isbn: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    publisher: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    institution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    authors: Mapped[list["LiteratureAuthor"]] = relationship(
        back_populates="literature",
        cascade="all, delete-orphan",
        order_by="LiteratureAuthor.author_order",
    )

    __table_args__ = (
        Index(
            "ix_literature_doi_normalized",
            text("lower(regexp_replace(doi, '^https?://(dx\\.)?doi\\.org/', ''))"),
        ),
        Index(
            "ix_literature_isbn_normalized",
            text("regexp_replace(isbn, '[- ]', '', 'g')"),
        ),
    )
