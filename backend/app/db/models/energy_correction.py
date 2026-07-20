from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    AppliedCorrectionComponentKind,
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
    EnergyUnit,
    FrequencyScaleKind,
    MeliusBacComponentKind,
)

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.level_of_theory import LevelOfTheory
    from app.db.models.literature import Literature
    from app.db.models.reaction import ReactionEntry
    from app.db.models.software import Software
    from app.db.models.species import ConformerObservation, SpeciesEntry
    from app.db.models.transition_state import TransitionStateEntry
    from app.db.models.workflow import WorkflowToolRelease


# ---------------------------------------------------------------------------
# Reference layer
# ---------------------------------------------------------------------------


class EnergyCorrectionScheme(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Reusable energy-correction parameter set.

    Examples: atom energies, atom enthalpy-of-formation references,
    Petersson BAC, Melius BAC, spin-orbit coupling constants.
    """

    __tablename__ = "energy_correction_scheme"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    kind: Mapped[EnergyCorrectionSchemeKind] = mapped_column(
        SAEnum(EnergyCorrectionSchemeKind, name="energy_correction_scheme_kind"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)

    level_of_theory_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("level_of_theory.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    source_literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    units: Mapped[Optional[EnergyUnit]] = mapped_column(
        SAEnum(EnergyUnit, name="energy_unit"),
        nullable=True,
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    level_of_theory: Mapped[Optional["LevelOfTheory"]] = relationship()
    source_literature: Mapped[Optional["Literature"]] = relationship()

    atom_params: Mapped[list["EnergyCorrectionSchemeAtomParam"]] = relationship(
        back_populates="scheme",
        cascade="all, delete-orphan",
    )
    bond_params: Mapped[list["EnergyCorrectionSchemeBondParam"]] = relationship(
        back_populates="scheme",
        cascade="all, delete-orphan",
    )
    component_params: Mapped[list["EnergyCorrectionSchemeComponentParam"]] = relationship(
        back_populates="scheme",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "uq_energy_correction_scheme_kind_name_lot_version",
            "kind",
            "name",
            "level_of_theory_id",
            "version",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class EnergyCorrectionSchemeAtomParam(Base):
    """Element-keyed scalar parameter within a correction scheme.

    Used for atom_hf, atom_thermal, SOC, atom_energies.
    """

    __tablename__ = "energy_correction_scheme_atom_param"

    scheme_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "energy_correction_scheme.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_energy_correction_scheme_atom_param_scheme_id",
        ),
        nullable=False,
    )
    element: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float] = mapped_column(Double, nullable=False)

    scheme: Mapped["EnergyCorrectionScheme"] = relationship(back_populates="atom_params")

    __table_args__ = (
        PrimaryKeyConstraint("scheme_id", "element"),
    )


class EnergyCorrectionSchemeBondParam(Base):
    """Bond-type keyed scalar parameter within a correction scheme.

    Used for Petersson BAC: C-H, C=C, O=S, etc.
    """

    __tablename__ = "energy_correction_scheme_bond_param"

    scheme_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "energy_correction_scheme.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_energy_correction_scheme_bond_param_scheme_id",
        ),
        nullable=False,
    )
    bond_key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float] = mapped_column(Double, nullable=False)

    scheme: Mapped["EnergyCorrectionScheme"] = relationship(back_populates="bond_params")

    __table_args__ = (
        PrimaryKeyConstraint("scheme_id", "bond_key"),
    )


class EnergyCorrectionSchemeComponentParam(Base):
    """Multi-component parameter within a correction scheme.

    Used for Melius BAC: atom_corr, bond_corr_length,
    bond_corr_neighbor, mol_corr.
    """

    __tablename__ = "energy_correction_scheme_component_param"

    scheme_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "energy_correction_scheme.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_energy_correction_scheme_component_param_scheme_id",
        ),
        nullable=False,
    )
    component_kind: Mapped[MeliusBacComponentKind] = mapped_column(
        SAEnum(MeliusBacComponentKind, name="melius_bac_component_kind"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float] = mapped_column(Double, nullable=False)

    scheme: Mapped["EnergyCorrectionScheme"] = relationship(
        back_populates="component_params"
    )

    __table_args__ = (
        PrimaryKeyConstraint("scheme_id", "component_kind", "key"),
    )


class FrequencyScaleFactor(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Immutable registry row for one frequency scale factor definition.

    Uniqueness is based on the full identity of the definition — LOT, software,
    scale kind, value, and provenance source — so two rows can legitimately have
    the same (LOT, software, scale_kind) with different values if they come from
    different sources.

    Null ``frequency_scale_factor_id`` on a statmech row means "unknown/not
    recorded".  A row with ``value = 1.0`` represents explicitly unscaled.
    """

    __tablename__ = "frequency_scale_factor"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    level_of_theory_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("level_of_theory.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    # Software dimension: same LOT in Gaussian vs QChem can yield different factors
    software_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("software.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    scale_kind: Mapped[FrequencyScaleKind] = mapped_column(
        SAEnum(FrequencyScaleKind, name="frequency_scale_kind"),
        nullable=False,
    )
    value: Mapped[float] = mapped_column(Double, nullable=False)

    source_literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    # Set when the factor was sourced from a workflow tool's data file (e.g. ARC's
    # freq_scale_factors.yml) rather than directly from a literature paper.
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "workflow_tool_release.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_frequency_scale_factor_workflow_tool_release_id",
        ),
        nullable=True,
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    level_of_theory: Mapped["LevelOfTheory"] = relationship()
    software: Mapped[Optional["Software"]] = relationship()
    source_literature: Mapped[Optional["Literature"]] = relationship()
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship()

    __table_args__ = (
        CheckConstraint("value > 0", name="value_gt_0"),
        Index(
            "uq_frequency_scale_factor_identity",
            "level_of_theory_id",
            "software_id",
            "scale_kind",
            "value",
            "source_literature_id",
            "workflow_tool_release_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


# ---------------------------------------------------------------------------
# Application layer
# ---------------------------------------------------------------------------


class AppliedEnergyCorrection(Base, TimestampMixin, CreatedByMixin):
    """One energy correction applied to a specific entry.

    Links exactly one provenance source (scheme XOR frequency scale factor)
    and exactly one target (species entry XOR reaction entry) to an applied
    result with a semantic application role.
    """

    __tablename__ = "applied_energy_correction"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Target FKs — exactly one must be populated
    target_species_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "species_entry.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_applied_energy_correction_target_species_entry_id",
        ),
        nullable=True,
    )
    target_reaction_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "reaction_entry.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_applied_energy_correction_target_reaction_entry_id",
        ),
        nullable=True,
    )
    target_transition_state_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "transition_state_entry.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_applied_energy_correction_target_transition_state_entry_id",
        ),
        nullable=True,
    )

    # Source FKs — provenance of what data was used
    source_conformer_observation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "conformer_observation.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_applied_energy_correction_source_conformer_observation_id",
        ),
        nullable=True,
    )
    source_calculation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    # Correction provenance — exactly one must be populated
    scheme_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("energy_correction_scheme.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    frequency_scale_factor_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "frequency_scale_factor.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_applied_energy_correction_frequency_scale_factor_id",
        ),
        nullable=True,
    )

    application_role: Mapped[EnergyCorrectionApplicationRole] = mapped_column(
        SAEnum(
            EnergyCorrectionApplicationRole,
            name="energy_correction_application_role",
        ),
        nullable=False,
    )

    value: Mapped[float] = mapped_column(Double, nullable=False)
    value_unit: Mapped[EnergyUnit] = mapped_column(
        SAEnum(EnergyUnit, name="energy_unit", create_type=False),
        nullable=False,
    )
    temperature_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    scheme: Mapped[Optional["EnergyCorrectionScheme"]] = relationship(
        foreign_keys=[scheme_id],
    )
    frequency_scale_factor: Mapped[Optional["FrequencyScaleFactor"]] = relationship(
        foreign_keys=[frequency_scale_factor_id],
    )
    target_species_entry: Mapped[Optional["SpeciesEntry"]] = relationship(
        foreign_keys=[target_species_entry_id],
    )
    target_reaction_entry: Mapped[Optional["ReactionEntry"]] = relationship(
        foreign_keys=[target_reaction_entry_id],
    )
    target_transition_state_entry: Mapped[Optional["TransitionStateEntry"]] = relationship(
        foreign_keys=[target_transition_state_entry_id],
    )
    source_conformer_observation: Mapped[Optional["ConformerObservation"]] = relationship(
        foreign_keys=[source_conformer_observation_id],
    )
    source_calculation: Mapped[Optional["Calculation"]] = relationship(
        foreign_keys=[source_calculation_id],
    )
    components: Mapped[list["AppliedEnergyCorrectionComponent"]] = relationship(
        back_populates="applied_correction",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "temperature_k IS NULL OR temperature_k > 0",
            name="temperature_k_gt_0",
        ),
        CheckConstraint(
            "num_nonnulls(target_species_entry_id, target_reaction_entry_id, "
            "target_transition_state_entry_id) = 1",
            name="exactly_one_target",
        ),
        CheckConstraint(
            "num_nonnulls(scheme_id, frequency_scale_factor_id) = 1",
            name="exactly_one_provenance_source",
        ),
        Index(
            "uq_applied_energy_correction_dedup",
            "target_species_entry_id",
            "target_reaction_entry_id",
            "target_transition_state_entry_id",
            "source_conformer_observation_id",
            "scheme_id",
            "frequency_scale_factor_id",
            "application_role",
            "temperature_k",
            "source_calculation_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class AppliedEnergyCorrectionComponent(Base):
    """Per-component breakdown of an applied energy correction.

    Records e.g. '6 x C-H bond using parameter -0.11, contribution -0.66'.
    """

    __tablename__ = "applied_energy_correction_component"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    applied_correction_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "applied_energy_correction.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_applied_energy_correction_component_applied_correction_id",
        ),
        nullable=False,
    )
    component_kind: Mapped[AppliedCorrectionComponentKind] = mapped_column(
        SAEnum(
            AppliedCorrectionComponentKind,
            name="applied_correction_component_kind",
        ),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    multiplicity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    parameter_value: Mapped[float] = mapped_column(Double, nullable=False)
    contribution_value: Mapped[float] = mapped_column(Double, nullable=False)

    applied_correction: Mapped["AppliedEnergyCorrection"] = relationship(
        back_populates="components"
    )

    __table_args__ = (
        CheckConstraint("multiplicity >= 1", name="multiplicity_ge_1"),
    )
