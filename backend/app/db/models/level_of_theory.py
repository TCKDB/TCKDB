from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import CHAR, BigInteger, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicRefMixin, TimestampMixin
from app.db.models.common import SpinTreatment

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation


class LevelOfTheory(Base, TimestampMixin, PublicRefMixin):
    """Method/basis provenance used by calculations."""

    __tablename__ = "level_of_theory"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    method: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aux_basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cabs_basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dispersion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    solvent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    solvent_model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Restricted / unrestricted / restricted-open — part of LOT identity
    # (DR-0034). NULL = unspecified (distinct from an explicit 'unknown').
    spin_treatment: Mapped[Optional[SpinTreatment]] = mapped_column(
        SAEnum(SpinTreatment, name="spin_treatment"),
        nullable=True,
    )

    lot_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)

    calculations: Mapped[list["Calculation"]] = relationship(back_populates="lot")

    __table_args__ = (UniqueConstraint("lot_hash"),)
