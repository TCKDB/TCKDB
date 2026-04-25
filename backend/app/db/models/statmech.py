from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import (
    RigidRotorKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    TorsionTreatmentKind,
)

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.energy_correction import FrequencyScaleFactor
    from app.db.models.literature import Literature
    from app.db.models.software import SoftwareRelease
    from app.db.models.species import SpeciesEntry
    from app.db.models.workflow import WorkflowToolRelease


class Statmech(Base, TimestampMixin, CreatedByMixin):
    """Statistical mechanics interpretation layer for a species entry."""

    __tablename__ = "statmech"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    species_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    scientific_origin: Mapped[ScientificOriginKind] = mapped_column(
        SAEnum(ScientificOriginKind, name="scientific_origin_kind"),
        nullable=False,
    )

    literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("workflow_tool_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    software_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("software_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    external_symmetry: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True
    )
    point_group: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_linear: Mapped[Optional[bool]] = mapped_column(nullable=True)
    rigid_rotor_kind: Mapped[Optional[RigidRotorKind]] = mapped_column(
        SAEnum(RigidRotorKind, name="rigid_rotor_kind"),
        nullable=True,
    )
    statmech_treatment: Mapped[Optional[StatmechTreatmentKind]] = mapped_column(
        SAEnum(StatmechTreatmentKind, name="statmech_treatment_kind"),
        nullable=True,
    )

    frequency_scale_factor_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("frequency_scale_factor.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    uses_projected_frequencies: Mapped[Optional[bool]] = mapped_column(nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    species_entry: Mapped["SpeciesEntry"] = relationship(
        back_populates="statmech_records"
    )
    literature: Mapped[Optional["Literature"]] = relationship()
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship(
        back_populates="statmech_records"
    )
    software_release: Mapped[Optional["SoftwareRelease"]] = relationship(
        back_populates="statmech_records"
    )
    frequency_scale_factor: Mapped[Optional["FrequencyScaleFactor"]] = relationship()

    source_calculations: Mapped[list["StatmechSourceCalculation"]] = relationship(
        back_populates="statmech",
        cascade="all, delete-orphan",
    )
    torsions: Mapped[list["StatmechTorsion"]] = relationship(
        back_populates="statmech",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "external_symmetry IS NULL OR external_symmetry >= 1",
            name="external_symmetry_ge_1",
        ),
    )


class StatmechSourceCalculation(Base):
    """Links statmech records to source calculations by role."""

    __tablename__ = "statmech_source_calculation"

    statmech_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("statmech.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    role: Mapped[StatmechCalculationRole] = mapped_column(
        SAEnum(StatmechCalculationRole, name="statmech_calc_role"),
        primary_key=True,
    )

    statmech: Mapped["Statmech"] = relationship(back_populates="source_calculations")
    calculation: Mapped["Calculation"] = relationship()


class StatmechTorsion(Base):
    """Stores one torsion associated with a statmech record."""

    __tablename__ = "statmech_torsion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    statmech_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("statmech.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    torsion_index: Mapped[int] = mapped_column(Integer, nullable=False)
    symmetry_number: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    treatment_kind: Mapped[Optional[TorsionTreatmentKind]] = mapped_column(
        SAEnum(TorsionTreatmentKind, name="torsion_treatment_kind"),
        nullable=True,
    )

    dimension: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    top_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    invalidated_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source_scan_calculation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    statmech: Mapped["Statmech"] = relationship(back_populates="torsions")
    source_scan_calculation: Mapped[Optional["Calculation"]] = relationship(
        foreign_keys=[source_scan_calculation_id]
    )
    coordinates: Mapped[list["StatmechTorsionDefinition"]] = relationship(
        back_populates="torsion",
        cascade="all, delete-orphan",
        order_by="StatmechTorsionDefinition.coordinate_index",
    )

    __table_args__ = (
        CheckConstraint("dimension >= 1", name="dimension_ge_1"),
        CheckConstraint("torsion_index >= 1", name="torsion_index_ge_1"),
        CheckConstraint(
            "symmetry_number IS NULL OR symmetry_number >= 1",
            name="symmetry_number_ge_1",
        ),
        Index(
            "uq_statmech_torsion_statmech_id",
            "statmech_id",
            "torsion_index",
            unique=True,
        ),
    )


class StatmechTorsionDefinition(Base):
    """Atom indices for a torsional coordinate."""

    __tablename__ = "statmech_torsion_definition"

    torsion_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("statmech_torsion.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    coordinate_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    atom1_index: Mapped[int] = mapped_column(Integer, nullable=False)
    atom2_index: Mapped[int] = mapped_column(Integer, nullable=False)
    atom3_index: Mapped[int] = mapped_column(Integer, nullable=False)
    atom4_index: Mapped[int] = mapped_column(Integer, nullable=False)

    torsion: Mapped["StatmechTorsion"] = relationship(back_populates="coordinates")

    __table_args__ = (
        CheckConstraint(
            "coordinate_index >= 1",
            name="coordinate_index_ge_1",
        ),
        CheckConstraint("atom1_index >= 1", name="atom1_index_ge_1"),
        CheckConstraint("atom2_index >= 1", name="atom2_index_ge_1"),
        CheckConstraint("atom3_index >= 1", name="atom3_index_ge_1"),
        CheckConstraint("atom4_index >= 1", name="atom4_index_ge_1"),
    )
