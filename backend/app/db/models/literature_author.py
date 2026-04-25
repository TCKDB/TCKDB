from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.author import Author
    from app.db.models.literature import Literature


class LiteratureAuthor(Base):
    __tablename__ = "literature_author"

    literature_id: Mapped[int] = mapped_column(
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("author.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    author_order: Mapped[int] = mapped_column(Integer, nullable=False)

    literature: Mapped["Literature"] = relationship(back_populates="authors")
    author: Mapped["Author"] = relationship(back_populates="literature_links")

    __table_args__ = (
        UniqueConstraint(
            "literature_id",
            "author_order",
            name="uq_literature_author_literature_id",
        ),
    )
