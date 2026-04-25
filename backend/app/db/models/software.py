from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.kinetics import Kinetics
    from app.db.models.network import Network
    from app.db.models.network_pdep import NetworkSolve
    from app.db.models.statmech import Statmech
    from app.db.models.thermo import Thermo
    from app.db.models.transport import Transport


class Software(Base, TimestampMixin):
    """Stable identity of a software package."""

    __tablename__ = "software"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    website: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    releases: Mapped[list["SoftwareRelease"]] = relationship(
        back_populates="software",
        cascade="all, delete-orphan",
    )

    __table_args__ = (UniqueConstraint("name"),)


class SoftwareRelease(Base, TimestampMixin):
    """Exact release metadata for a software package.

    Dedupe uses `(software_id, version, revision, build)` with PostgreSQL
    `NULLS NOT DISTINCT`.
    """

    __tablename__ = "software_release"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    software_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("software.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    revision: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    build: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    release_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    software: Mapped["Software"] = relationship(back_populates="releases")

    calculations: Mapped[list["Calculation"]] = relationship(
        back_populates="software_release"
    )
    thermo_records: Mapped[list["Thermo"]] = relationship(
        back_populates="software_release"
    )
    statmech_records: Mapped[list["Statmech"]] = relationship(
        back_populates="software_release"
    )
    transport_records: Mapped[list["Transport"]] = relationship(
        back_populates="software_release"
    )
    kinetics_records: Mapped[list["Kinetics"]] = relationship(
        back_populates="software_release"
    )
    networks: Mapped[list["Network"]] = relationship(back_populates="software_release")
    network_solves: Mapped[list["NetworkSolve"]] = relationship(
        back_populates="software_release"
    )

    __table_args__ = (
        Index(
            "uq_software_release_software_id",
            "software_id",
            "version",
            "revision",
            "build",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )
