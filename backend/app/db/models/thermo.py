from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    PrimaryKeyConstraint,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import PhaseKind, ScientificOriginKind, ThermoCalculationRole

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.literature import Literature
    from app.db.models.software import SoftwareRelease
    from app.db.models.species import SpeciesEntry
    from app.db.models.statmech import Statmech
    from app.db.models.workflow import WorkflowToolRelease


class Thermo(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Thermochemistry records for a species entry.

    Reference-state semantics (added 2026-07-15). The scalar and fitted
    values on this row are standard-state quantities:

    * ``h298_kj_mol`` / ``s298_j_mol_k`` are the standard enthalpy of
      formation and standard entropy at 298.15 K.
    * ``enthalpy_formation_0k_kj_mol`` is the 0 K standard formation
      enthalpy (ΔfH°(0 K)), the quantity computational thermochemistry
      derives directly from atomization/composite energies. It relates to
      ``h298_kj_mol`` through the species and element thermal enthalpy
      increments, ΔfH°(298) = ΔfH°(0) + [H°(298) − H°(0)]_species −
      Σ [H°(298) − H°(0)]_elements; the two are stored side by side
      rather than derived from one another because either may be the
      primary reported value.
    * ``reference_pressure_bar`` is the standard-state pressure the H/S
      and NASA/tabulated values are referenced to (IUPAC 1 bar; legacy
      data using the older 1 atm convention should record 1.01325).
      ``NULL`` means the reference pressure was not specified.
    * ``phase`` records the physical phase (gas by default for computed
      species). ``NULL`` means unspecified.
    * ``statmech_id`` links a *computed* thermo row to the ``statmech``
      record it was derived from. ``NULL`` for experimental, literature,
      or group-additivity thermo that has no statmech basis.

    All of these are nullable and additive; legacy rows keep their
    original (now explicitly under-specified) semantics.
    """

    __tablename__ = "thermo"

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

    h298_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    s298_j_mol_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    h298_uncertainty_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    s298_uncertainty_j_mol_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    # 0 K standard formation enthalpy (ΔfH°(0 K)) and its uncertainty.
    enthalpy_formation_0k_kj_mol: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )
    enthalpy_formation_0k_uncertainty_kj_mol: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )

    # Standard-state reference pressure (fixed unit: bar). NULL = unspecified.
    reference_pressure_bar: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )
    # Physical phase the record is referenced to. NULL = unspecified.
    phase: Mapped[Optional[PhaseKind]] = mapped_column(
        SAEnum(PhaseKind, name="phase_kind"),
        nullable=True,
    )

    tmin_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    tmax_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    # Statmech record this computed thermo was derived from. NULL for
    # experimental / literature / group-additivity thermo.
    statmech_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("statmech.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    species_entry: Mapped["SpeciesEntry"] = relationship(
        back_populates="thermo_records"
    )
    statmech: Mapped[Optional["Statmech"]] = relationship(
        back_populates="thermo_records"
    )
    literature: Mapped[Optional["Literature"]] = relationship()
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship(
        back_populates="thermo_records"
    )
    software_release: Mapped[Optional["SoftwareRelease"]] = relationship(
        back_populates="thermo_records"
    )

    points: Mapped[list["ThermoPoint"]] = relationship(
        back_populates="thermo",
        cascade="all, delete-orphan",
        order_by="ThermoPoint.temperature_k",
    )
    nasa: Mapped[Optional["ThermoNASA"]] = relationship(
        back_populates="thermo",
        cascade="all, delete-orphan",
        uselist=False,
    )
    source_calculations: Mapped[list["ThermoSourceCalculation"]] = relationship(
        back_populates="thermo",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("tmin_k IS NULL OR tmin_k > 0", name="tmin_k_gt_0"),
        CheckConstraint("tmax_k IS NULL OR tmax_k > 0", name="tmax_k_gt_0"),
        CheckConstraint(
            "tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k",
            name="tmin_le_tmax",
        ),
        CheckConstraint(
            "h298_uncertainty_kj_mol IS NULL OR h298_uncertainty_kj_mol >= 0",
            name="h298_uncertainty_ge_0",
        ),
        CheckConstraint(
            "s298_uncertainty_j_mol_k IS NULL OR s298_uncertainty_j_mol_k >= 0",
            name="s298_uncertainty_ge_0",
        ),
        CheckConstraint(
            "enthalpy_formation_0k_uncertainty_kj_mol IS NULL "
            "OR enthalpy_formation_0k_uncertainty_kj_mol >= 0",
            name="enthalpy_formation_0k_uncertainty_ge_0",
        ),
    )


class ThermoPoint(Base):
    """Tabulated thermo values at a specific temperature."""

    __tablename__ = "thermo_point"

    thermo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thermo.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    temperature_k: Mapped[float] = mapped_column(Double, nullable=False)

    cp_j_mol_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    h_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    s_j_mol_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    g_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    thermo: Mapped["Thermo"] = relationship(back_populates="points")

    __table_args__ = (PrimaryKeyConstraint("thermo_id", "temperature_k"),)


class ThermoNASA(Base):
    """NASA polynomial coefficients for a thermo record."""

    __tablename__ = "thermo_nasa"

    thermo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thermo.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )

    t_low: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    t_mid: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    t_high: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    a1: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a2: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a3: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a4: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a5: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a6: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a7: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    b1: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    b2: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    b3: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    b4: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    b5: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    b6: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    b7: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    thermo: Mapped["Thermo"] = relationship(back_populates="nasa")

    __table_args__ = (
        CheckConstraint("t_low IS NULL OR t_low > 0", name="t_low_gt_0"),
        CheckConstraint(
            """
            (
                t_low IS NULL
                AND t_mid IS NULL
                AND t_high IS NULL
            )
            OR
            (
                t_low IS NOT NULL
                AND t_mid IS NOT NULL
                AND t_high IS NOT NULL
            )
            """,
            name="temperature_bounds_all_or_none",
        ),
        CheckConstraint(
            "t_low IS NULL OR t_mid IS NULL OR t_mid > t_low",
            name="t_mid_gt_t_low",
        ),
        CheckConstraint(
            "t_mid IS NULL OR t_high IS NULL OR t_high > t_mid",
            name="t_high_gt_t_mid",
        ),
    )


class ThermoSourceCalculation(Base):
    """Links thermo records to supporting calculations by role."""

    __tablename__ = "thermo_source_calculation"

    thermo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thermo.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    role: Mapped[ThermoCalculationRole] = mapped_column(
        SAEnum(ThermoCalculationRole, name="thermo_calc_role"),
        nullable=False,
    )

    thermo: Mapped["Thermo"] = relationship(back_populates="source_calculations")
    calculation: Mapped["Calculation"] = relationship()

    __table_args__ = (PrimaryKeyConstraint("thermo_id", "calculation_id", "role"),)
