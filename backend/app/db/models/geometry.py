from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import CHAR, BigInteger, CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.calculation import (
        CalculationInputGeometry,
        CalculationOutputGeometry,
    )


class Geometry(Base, TimestampMixin):
    """Stores a reusable molecular geometry and its serialized XYZ form."""

    __tablename__ = "geometry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    natoms: Mapped[int] = mapped_column(Integer, nullable=False)
    geom_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    xyz_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    atoms: Mapped[list["GeometryAtom"]] = relationship(
        back_populates="geometry",
        cascade="all, delete-orphan",
    )

    calculation_outputs: Mapped[list["CalculationOutputGeometry"]] = relationship(
        back_populates="geometry",
    )
    calculation_inputs: Mapped[list["CalculationInputGeometry"]] = relationship(
        back_populates="geometry",
    )

    __table_args__ = (CheckConstraint("natoms >= 1", name="natoms_ge_1"),)


class GeometryAtom(Base):
    """Stores per-atom coordinates for a geometry row."""

    __tablename__ = "geometry_atom"

    geometry_id: Mapped[int] = mapped_column(
        ForeignKey("geometry.id"),
        primary_key=True,
    )

    atom_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    element: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    x: Mapped[float] = mapped_column(nullable=False)
    y: Mapped[float] = mapped_column(nullable=False)
    z: Mapped[float] = mapped_column(nullable=False)

    geometry: Mapped[Geometry] = relationship(back_populates="atoms")

    __table_args__ = (CheckConstraint("atom_index >= 1", name="atom_index_ge_1"),)
