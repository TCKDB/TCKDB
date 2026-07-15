from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
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
    from app.db.models.thermo import Thermo
    from app.db.models.workflow import WorkflowToolRelease


class Statmech(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Statistical mechanics interpretation layer for a species entry.

    Rotational constants convention: ``rotational_constant_{a,b,c}_cm1``
    hold the reported principal rotational constants (unit cm⁻¹), stored in
    the order provided by the source — conventionally descending A ≥ B ≥ C
    for an asymmetric top. The count of non-null values corresponds to the
    rotor's rotational degrees of freedom as classified by
    ``rigid_rotor_kind`` / ``is_linear`` (e.g. a linear rotor reports a
    single constant in ``_a``, a symmetric/spherical top may report two/one
    distinct values). No ordering constraint is imposed at the DB level:
    floats plus the linear/symmetric cases make an ``a >= b >= c`` check
    inappropriate. Values are stored as parsed observations, as-is.
    """

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
        index=True,
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

    # Reported principal rotational constants (cm⁻¹), stored as-provided.
    # See the class docstring for the storage convention.
    rotational_constant_a_cm1: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )
    rotational_constant_b_cm1: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )
    rotational_constant_c_cm1: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
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
    # Number of optical isomers (enantiomers). Contributes R*ln(n) to the
    # entropy (DR-0033). NULL = unspecified; 1 = achiral / no contribution.
    optical_isomers: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
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
    electronic_levels: Mapped[list["StatmechElectronicLevel"]] = relationship(
        back_populates="statmech",
        order_by="StatmechElectronicLevel.level_index",
        cascade="all, delete-orphan",
    )
    # Computed thermo records derived from this statmech interpretation.
    thermo_records: Mapped[list["Thermo"]] = relationship(
        back_populates="statmech"
    )

    __table_args__ = (
        CheckConstraint(
            "external_symmetry IS NULL OR external_symmetry >= 1",
            name="external_symmetry_ge_1",
        ),
        CheckConstraint(
            "optical_isomers IS NULL OR optical_isomers >= 1",
            name="optical_isomers_ge_1",
        ),
        CheckConstraint(
            "rotational_constant_a_cm1 IS NULL OR rotational_constant_a_cm1 > 0",
            name="rotational_constant_a_cm1_positive",
        ),
        CheckConstraint(
            "rotational_constant_b_cm1 IS NULL OR rotational_constant_b_cm1 > 0",
            name="rotational_constant_b_cm1_positive",
        ),
        CheckConstraint(
            "rotational_constant_c_cm1 IS NULL OR rotational_constant_c_cm1 > 0",
            name="rotational_constant_c_cm1_positive",
        ),
    )


class StatmechElectronicLevel(Base):
    """One electronic energy level for the electronic partition function.

    q_elec = Σ gᵢ·exp(−εᵢ/kT). Store the low-lying electronic states as
    ordered (energy, degeneracy) pairs (DR-0033): the ground state is
    ``level_index=1`` at ``energy_cm1=0`` with its degeneracy; excited
    states follow. Needed for open-shell atoms/radicals with low-lying
    states (OH ²Π spin-orbit splitting, O(³P), halogen atoms) where a bare
    term symbol is insufficient.
    """

    __tablename__ = "statmech_electronic_level"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    statmech_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("statmech.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    level_index: Mapped[int] = mapped_column(Integer, nullable=False)
    energy_cm1: Mapped[float] = mapped_column(Double, nullable=False)
    degeneracy: Mapped[int] = mapped_column(Integer, nullable=False)

    statmech: Mapped["Statmech"] = relationship(back_populates="electronic_levels")

    __table_args__ = (
        UniqueConstraint(
            "statmech_id", "level_index", name="uq_statmech_electronic_level"
        ),
        CheckConstraint("level_index >= 1", name="level_index_ge_1"),
        CheckConstraint("energy_cm1 >= 0", name="energy_cm1_ge_0"),
        CheckConstraint("degeneracy >= 1", name="degeneracy_ge_1"),
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
