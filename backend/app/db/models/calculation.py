from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
    and_,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    IMAGINARY_MODE_TAU_BASIS_VALUES,
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConstraintKind,
    CoordinateUnit,
    HessianSource,
    ImaginaryModeDisposition,
    IRCDirection,
    ParameterSource,
    PathSearchMethod,
    ScanCoordinateKind,
    SCFStabilityStatus,
    SoftwareReconciliationStatus,
    SubmissionRecordType,
    ValidationStatus,
)
from app.db.models.record_review import RecordReview

if TYPE_CHECKING:
    from app.db.models.execution_environment import ExecutionEnvironmentManifest
    from app.db.models.geometry import Geometry
    from app.db.models.level_of_theory import LevelOfTheory
    from app.db.models.literature import Literature
    from app.db.models.software import SoftwareRelease
    from app.db.models.species import ConformerObservation, SpeciesEntry
    from app.db.models.transition_state import TransitionStateEntry
    from app.db.models.workflow import WorkflowToolRelease


class Calculation(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Computational record with one scientific owner and an optional observation anchor."""

    __tablename__ = "calculation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    type: Mapped[CalculationType] = mapped_column(
        SAEnum(CalculationType, name="calc_type"),
        nullable=False,
    )
    quality: Mapped[CalculationQuality] = mapped_column(
        SAEnum(CalculationQuality, name="calc_quality"),
        nullable=False,
        default=CalculationQuality.raw,
        server_default=CalculationQuality.raw.value,
    )

    species_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )
    transition_state_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("transition_state_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )

    software_release_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("software_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("workflow_tool_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )
    lot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("level_of_theory.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )

    literature_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )
    execution_environment_manifest_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "execution_environment_manifest.id",
            name="fk_calculation_execution_environment_manifest",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
        index=True,
    )

    conformer_observation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "conformer_observation.id", deferrable=True, initially="IMMEDIATE"
        ),
        nullable=True,
        index=True,
        doc="Optional anchor to the specific conformer observation this calculation belongs to.",
    )

    parameters_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, doc="Parsed parameter snapshot from ESS input/output"
    )
    parameters_parser_version: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Version tag of the parser that extracted parameters"
    )
    parameters_extracted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True, doc="When parameters were extracted"
    )

    # --- Software provenance reconciliation (DR-0008) -----------------------
    # Records how the user-declared software_release related to the version
    # banner observed by the ESS output parser. Provenance, not a trust gate:
    # a mismatch is recorded here (declared value still wins on
    # ``software_release_id``), never used to reject the upload. NULL means
    # reconciliation was never run for this row.
    software_reconciliation_status: Mapped[
        Optional[SoftwareReconciliationStatus]
    ] = mapped_column(
        SAEnum(SoftwareReconciliationStatus, name="software_reconciliation_status"),
        nullable=True,
        index=True,
        doc="Outcome of declared-vs-observed software provenance reconciliation.",
    )
    observed_software_banner: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Raw software version banner observed by the ESS output parser, when available.",
    )
    declared_software_banner: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc=(
            "Compact rendering of the originally-declared software_release "
            "(name/version/revision/build), preserved here only when "
            "software_reconciliation_status='mismatch' identified a "
            "different program and software_release_id was repointed at "
            "the parser-observed release. NULL otherwise -- the declared "
            "release is still reachable via software_release_id whenever "
            "nothing overrode it."
        ),
    )

    species_entry: Mapped[Optional["SpeciesEntry"]] = relationship(
        back_populates="calculations",
        foreign_keys=[species_entry_id],
    )
    transition_state_entry: Mapped[Optional["TransitionStateEntry"]] = relationship(
        back_populates="calculations",
        foreign_keys=[transition_state_entry_id],
    )
    software_release: Mapped[Optional["SoftwareRelease"]] = relationship(
        back_populates="calculations"
    )
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship(
        back_populates="calculations"
    )
    lot: Mapped[Optional["LevelOfTheory"]] = relationship(back_populates="calculations")
    literature: Mapped[Optional["Literature"]] = relationship()
    execution_environment_manifest: Mapped[Optional["ExecutionEnvironmentManifest"]] = relationship(
        back_populates="calculations"
    )
    conformer_observation: Mapped[Optional["ConformerObservation"]] = relationship(
        back_populates="calculations",
        foreign_keys=[conformer_observation_id],
    )

    input_geometries: Mapped[list["CalculationInputGeometry"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        order_by="CalculationInputGeometry.input_order",
    )
    output_geometries: Mapped[list["CalculationOutputGeometry"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
    )
    parent_dependencies: Mapped[list["CalculationDependency"]] = relationship(
        back_populates="parent_calculation",
        foreign_keys="CalculationDependency.parent_calculation_id",
        cascade="all, delete-orphan",
    )
    child_dependencies: Mapped[list["CalculationDependency"]] = relationship(
        back_populates="child_calculation",
        foreign_keys="CalculationDependency.child_calculation_id",
        cascade="all, delete-orphan",
    )

    sp_result: Mapped[Optional["CalculationSPResult"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        uselist=False,
    )
    opt_result: Mapped[Optional["CalculationOptResult"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        uselist=False,
    )
    freq_result: Mapped[Optional["CalculationFreqResult"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        uselist=False,
    )
    scan_result: Mapped[Optional["CalculationScanResult"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        uselist=False,
    )
    scan_coordinates: Mapped[list["CalculationScanCoordinate"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        order_by="CalculationScanCoordinate.coordinate_index",
    )
    constraints: Mapped[list["CalculationConstraint"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        order_by="CalculationConstraint.constraint_index",
    )
    scan_points: Mapped[list["CalculationScanPoint"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        order_by="CalculationScanPoint.point_index",
    )
    irc_result: Mapped[Optional["CalculationIRCResult"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        uselist=False,
    )
    irc_points: Mapped[list["CalculationIRCPoint"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        order_by="CalculationIRCPoint.point_index",
    )
    path_search_result: Mapped[Optional["CalculationPathSearchResult"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        uselist=False,
    )
    path_search_points: Mapped[list["CalculationPathSearchPoint"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        order_by="CalculationPathSearchPoint.point_index",
    )
    artifacts: Mapped[list["CalculationArtifact"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
    )
    parameters: Mapped[list["CalculationParameter"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
    )
    scf_stability: Mapped[Optional["CalculationSCFStability"]] = relationship(
        back_populates="calculation",
        foreign_keys="CalculationSCFStability.calculation_id",
        uselist=False,
        cascade="all, delete-orphan",
    )
    hessian: Mapped[Optional["CalculationHessian"]] = relationship(
        back_populates="calculation",
        foreign_keys="CalculationHessian.calculation_id",
        uselist=False,
        cascade="all, delete-orphan",
    )
    wavefunction_diagnostic: Mapped[
        Optional["CalculationWavefunctionDiagnostic"]
    ] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        uselist=False,
    )
    spin_diagnostic: Mapped[Optional["CalculationSpinDiagnostic"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        uselist=False,
    )
    geometry_validation: Mapped[Optional["CalculationGeometryValidation"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        uselist=False,
    )

    #: The current curator-authored review/trust state for this calculation,
    #: if one has been recorded.
    #:
    #: Joined on ``(record_type, record_id)``, not a foreign key, because
    #: ``record_review`` is a generic polymorphic association shared by every
    #: reviewable record type (species, reaction, thermo, kinetics, ...) —
    #: see :class:`app.db.models.common.SubmissionRecordType`. A real FK
    #: column on this table would have to be duplicated on every other
    #: reviewable table for the same shape of relationship.
    #:
    #: ``viewonly=True`` and no ``lazy="selectin"``: unlike
    #: :attr:`CalculationArtifact.integrity_events`, this relationship is not
    #: needed on every calculation load — only where a trust evaluation is
    #: being built (see ``app/services/trust/evaluator.py`` and
    #: ``app/services/scientific_read/calculations.py``'s
    #: ``_TRUST_EAGER_LOADS``), which must eager-load it explicitly with
    #: ``selectinload(Calculation.record_review)``. The trust evaluator's
    #: check runners are contractually pure and issue no queries of their
    #: own (see ``app/services/trust/rubrics.py::_check_quality_recorded``),
    #: so the caller loading a "pure" graph must supply this row already
    #: attached rather than relying on a lazy load.
    record_review: Mapped[Optional["RecordReview"]] = relationship(
        primaryjoin=lambda: and_(
            foreign(RecordReview.record_id) == Calculation.id,
            RecordReview.record_type == SubmissionRecordType.calculation,
        ),
        viewonly=True,
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            """
                (
                    transition_state_entry_id IS NOT NULL
                    AND species_entry_id IS NULL
                )
                OR
                (
                    transition_state_entry_id IS NULL
                    AND species_entry_id IS NOT NULL
                )
                """,
            name="one_owner",
        ),
        # The analytics filter pass selects only ``id`` for a given
        # ``type``, always bounded by ``id <= watermark``. Both columns in
        # the index make that scan index-only; see revision
        # a7c2e4f8b6d9 for the measured plans.
        Index("ix_calculation_type_id", "type", "id"),
    )


class CalculationInputGeometry(Base):
    """Ordered input-geometry link table for a calculation."""

    __tablename__ = "calculation_input_geometry"

    calculation_id: Mapped[int] = mapped_column(
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    geometry_id: Mapped[int] = mapped_column(
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    input_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    calculation: Mapped["Calculation"] = relationship(back_populates="input_geometries")
    geometry: Mapped["Geometry"] = relationship(back_populates="calculation_inputs")

    __table_args__ = (
        PrimaryKeyConstraint("calculation_id", "input_order"),
        UniqueConstraint(
            "calculation_id",
            "geometry_id",
            name="uq_calculation_input_geometry_calculation_id",
        ),
        CheckConstraint("input_order >= 1", name="input_order_ge_1"),
    )


class CalculationOutputGeometry(Base):
    __tablename__ = "calculation_output_geometry"

    calculation_id: Mapped[int] = mapped_column(
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    geometry_id: Mapped[int] = mapped_column(
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    output_order: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    role: Mapped[Optional[CalculationGeometryRole]] = mapped_column(
        SAEnum(CalculationGeometryRole, name="calculation_geometry_role"),
        nullable=True,
    )

    calculation: Mapped["Calculation"] = relationship(
        back_populates="output_geometries"
    )
    geometry: Mapped["Geometry"] = relationship(back_populates="calculation_outputs")

    __table_args__ = (
        UniqueConstraint(
            "calculation_id",
            "geometry_id",
            name="uq_calculation_output_geometry_calculation_id",
        ),
        CheckConstraint("output_order >= 1", name="output_order_ge_1"),
    )


class CalculationDependency(Base):
    """Directed dependency edge between two calculations.

    Self-edges are forbidden in the schema. Stronger role-specific parent-count
    rules or full DAG validation belong in application logic unless the policy
    is narrowed enough for partial unique indexes. Selected roles currently
    enforce at most one parent per child: `optimized_from`, `freq_on`,
    `single_point_on`, and `scan_parent`.
    """

    __tablename__ = "calculation_dependency"

    parent_calculation_id: Mapped[int] = mapped_column(
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    child_calculation_id: Mapped[int] = mapped_column(
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    dependency_role: Mapped[CalculationDependencyRole] = mapped_column(
        SAEnum(CalculationDependencyRole, name="calculation_dependency_role"),
        nullable=False,
    )

    parent_calculation: Mapped["Calculation"] = relationship(
        back_populates="parent_dependencies",
        foreign_keys=[parent_calculation_id],
    )
    child_calculation: Mapped["Calculation"] = relationship(
        back_populates="child_dependencies",
        foreign_keys=[child_calculation_id],
    )

    __table_args__ = (
        CheckConstraint(
            "parent_calculation_id <> child_calculation_id",
            name="not_self",
        ),
        Index(
            "uq_calculation_dependency_child_calculation_id_optimized_from",
            "child_calculation_id",
            unique=True,
            postgresql_where=text("dependency_role = 'optimized_from'"),
        ),
        Index(
            "uq_calculation_dependency_child_calculation_id_freq_on",
            "child_calculation_id",
            unique=True,
            postgresql_where=text("dependency_role = 'freq_on'"),
        ),
        Index(
            "uq_calculation_dependency_child_calculation_id_single_point_on",
            "child_calculation_id",
            unique=True,
            postgresql_where=text("dependency_role = 'single_point_on'"),
        ),
        Index(
            "uq_calculation_dependency_child_calculation_id_scan_parent",
            "child_calculation_id",
            unique=True,
            postgresql_where=text("dependency_role = 'scan_parent'"),
        ),
    )


class CalculationSPResult(Base):
    __tablename__ = "calc_sp_result"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    electronic_energy_hartree: Mapped[Optional[float]] = mapped_column(nullable=True)
    electronic_energy_uncertainty_hartree: Mapped[Optional[float]] = mapped_column(
        nullable=True
    )

    calculation: Mapped["Calculation"] = relationship(back_populates="sp_result")


class CalculationOptResult(Base):
    __tablename__ = "calc_opt_result"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    converged: Mapped[Optional[bool]] = mapped_column(nullable=True)
    n_steps: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    final_energy_hartree: Mapped[Optional[float]] = mapped_column(nullable=True)

    calculation: Mapped["Calculation"] = relationship(back_populates="opt_result")

    __table_args__ = (
        CheckConstraint("n_steps IS NULL OR n_steps >= 0", name="n_steps_ge_0"),
    )


class CalculationFreqResult(Base):
    __tablename__ = "calc_freq_result"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    n_imag: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    imag_freq_cm1: Mapped[Optional[float]] = mapped_column(nullable=True)
    zpe_hartree: Mapped[Optional[float]] = mapped_column(nullable=True)
    zpe_uncertainty_hartree: Mapped[Optional[float]] = mapped_column(nullable=True)

    #: ``mode_index`` of the mode the depositor designated the reaction
    #: coordinate. ADR 0012 makes this the contract that replaced the
    #: ``n_imag == 1`` gate, and persisting it is what lets the read-time
    #: trust rubric *cite* the upload-time judgement instead of
    #: re-deriving it from ``n_imag`` and disagreeing with it. NULL on
    #: every minimum and on any transition state with a single imaginary
    #: mode, where there is nothing to disambiguate.
    reaction_coordinate_mode_index: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    #: The ADR 0012 tolerance actually applied to this record's extra
    #: imaginary modes (cm⁻¹), and which row of the protocol table chose
    #: it. Stored rather than recomputed because τ is read from execution
    #: provenance: a later parser improvement would silently re-decide
    #: every historical record, and ADR 0012's whole point is that a
    #: reader can see what was decided and on what basis.
    imaginary_mode_tau_cm1: Mapped[Optional[float]] = mapped_column(nullable=True)
    #: Typed ``str`` rather than the ``TauBasis`` enum **on purpose**, and
    #: only on the wire: a reader must be shown a basis this build does
    #: not recognise rather than have the whole record refused for it.
    #: That argument does not extend to the write side, where an
    #: unrecognised value is a typo rather than a newer writer, so the
    #: vocabulary is constrained in the database by
    #: ``imaginary_mode_tau_basis_known`` below. A CHECK rather than a
    #: native enum because the column stays ``TEXT`` in the ORM — and
    #: because a CHECK, unlike a foreign key, still holds under
    #: ``session_replication_role = replica``, which is what bulk loaders
    #: and restore paths run under (see
    #: ``tests/db/test_element_symbol_canonicality.py``).
    imaginary_mode_tau_basis: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    #: ADR 0012's structural flag: this record carries an imaginary mode
    #: at or above τ beyond its reaction coordinate, so it is a genuine
    #: higher-order saddle. Accepted, because that can be correct
    #: chemistry, but excluded from default transition-state consumption
    #: unless explicitly opted into.
    imaginary_mode_structural_flag: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )

    calculation: Mapped["Calculation"] = relationship(back_populates="freq_result")
    modes: Mapped[list["CalculationFreqMode"]] = relationship(
        primaryjoin=(
            "CalculationFreqResult.calculation_id == "
            "foreign(CalculationFreqMode.calculation_id)"
        ),
        order_by="CalculationFreqMode.mode_index",
        viewonly=True,
    )

    __table_args__ = (
        CheckConstraint(
            "imaginary_mode_tau_basis IS NULL OR imaginary_mode_tau_basis IN ("
            + ", ".join(f"'{value}'" for value in IMAGINARY_MODE_TAU_BASIS_VALUES)
            + ")",
            name="imaginary_mode_tau_basis_known",
        ),
    )


class CalculationFreqMode(Base):
    """One vibrational mode parsed from a frequency calculation.

    Imaginary modes are stored as negative ``frequency_cm1`` together
    with ``is_imaginary = true``. The flag is redundant with the sign
    but keeps query intent explicit (``WHERE is_imaginary``) and makes
    ingestion bugs that drop the sign survivable.
    """

    __tablename__ = "calc_freq_mode"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    mode_index: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency_cm1: Mapped[float] = mapped_column(nullable=False)
    is_imaginary: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reduced_mass_amu: Mapped[Optional[float]] = mapped_column(nullable=True)
    force_constant_mdyne_angstrom: Mapped[Optional[float]] = mapped_column(
        nullable=True
    )
    ir_intensity_km_mol: Mapped[Optional[float]] = mapped_column(nullable=True)
    raman_activity: Mapped[Optional[float]] = mapped_column(nullable=True)
    symmetry_label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: What this imaginary mode is, when it is not the reaction
    #: coordinate — declared by the depositor, never inferred. ADR 0012
    #: accepts extra imaginary modes on a transition state only because
    #: the record says what they are.
    #:
    #: Declared, and — since 2026-08-11 — *checkable*. ADR 0013 held that
    #: TCKDB could not compute the assignment itself because it stores no
    #: displacement vectors; that was wrong, because ``calc_hessian``
    #: stores the matrix whose eigenvectors they are. The ADR 0012
    #: projections run at read time from that matrix
    #: (``include=imaginary_mode_projections``) and report a determination
    #: *beside* this column, never in place of it. A conflict between the
    #: two is surfaced and left for a curator: this column keeps saying
    #: exactly what the depositor deposited.
    #:
    #: The determination is available only where a Hessian is. Elsewhere
    #: it reads "not determinable", which is a different answer from
    #: "the declaration checks out".
    imaginary_disposition: Mapped[Optional[ImaginaryModeDisposition]] = mapped_column(
        SAEnum(ImaginaryModeDisposition, name="imaginary_mode_disposition"),
        nullable=True,
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("calculation_id", "mode_index"),
        CheckConstraint(
            "imaginary_disposition IS NULL OR is_imaginary",
            name="imaginary_disposition_requires_imaginary_mode",
        ),
        CheckConstraint("mode_index >= 1", name="mode_index_ge_1"),
        CheckConstraint(
            "reduced_mass_amu IS NULL OR reduced_mass_amu > 0",
            name="reduced_mass_amu_gt_0",
        ),
        CheckConstraint(
            "ir_intensity_km_mol IS NULL OR ir_intensity_km_mol >= 0",
            name="ir_intensity_km_mol_ge_0",
        ),
        CheckConstraint(
            "(is_imaginary AND frequency_cm1 < 0) "
            "OR (NOT is_imaginary AND frequency_cm1 >= 0)",
            name="frequency_sign_matches_is_imaginary",
        ),
    )


class CalculationScanResult(Base):
    """Scan-level metadata for a scan calculation."""

    __tablename__ = "calc_scan_result"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    is_relaxed: Mapped[Optional[bool]] = mapped_column(nullable=True)
    zero_energy_reference_hartree: Mapped[Optional[float]] = mapped_column(
        nullable=True
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(back_populates="scan_result")
    coordinates: Mapped[list["CalculationScanCoordinate"]] = relationship(
        primaryjoin=(
            "CalculationScanResult.calculation_id == "
            "foreign(CalculationScanCoordinate.calculation_id)"
        ),
        viewonly=True,
        order_by="CalculationScanCoordinate.coordinate_index",
    )
    constraints: Mapped[list["CalculationConstraint"]] = relationship(
        primaryjoin=(
            "CalculationScanResult.calculation_id == "
            "foreign(CalculationConstraint.calculation_id)"
        ),
        viewonly=True,
        order_by="CalculationConstraint.constraint_index",
    )
    points: Mapped[list["CalculationScanPoint"]] = relationship(
        primaryjoin=(
            "CalculationScanResult.calculation_id == "
            "foreign(CalculationScanPoint.calculation_id)"
        ),
        viewonly=True,
        order_by="CalculationScanPoint.point_index",
    )

    __table_args__ = (CheckConstraint("dimension >= 1", name="dimension_ge_1"),)


class CalculationScanCoordinate(Base):
    """Definition of one scanned internal coordinate.

    Supports variable-arity coordinates: bond (2 atoms), angle (3),
    dihedral/improper (4).  ``atom3_index`` and ``atom4_index`` are
    nullable; check constraints enforce correct arity per
    ``coordinate_kind``.
    """

    __tablename__ = "calc_scan_coordinate"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    coordinate_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    coordinate_kind: Mapped[ScanCoordinateKind] = mapped_column(
        SAEnum(ScanCoordinateKind, name="scan_coordinate_kind"),
        nullable=False,
    )
    atom1_index: Mapped[int] = mapped_column(Integer, nullable=False)
    atom2_index: Mapped[int] = mapped_column(Integer, nullable=False)
    atom3_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    atom4_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    step_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    step_size: Mapped[Optional[float]] = mapped_column(nullable=True)
    start_value: Mapped[Optional[float]] = mapped_column(nullable=True)
    end_value: Mapped[Optional[float]] = mapped_column(nullable=True)
    value_unit: Mapped[Optional[CoordinateUnit]] = mapped_column(
        SAEnum(CoordinateUnit, name="coordinate_unit"),
        nullable=True,
    )
    resolution_degrees: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    symmetry_number: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)

    calculation: Mapped["Calculation"] = relationship(back_populates="scan_coordinates")
    point_coordinate_values: Mapped[list["CalculationScanPointCoordinateValue"]] = (
        relationship(
            back_populates="coordinate",
            cascade="all, delete-orphan",
            overlaps="coordinate_values,scan_point",
        )
    )

    __table_args__ = (
        CheckConstraint("coordinate_index >= 1", name="coordinate_index_ge_1"),
        CheckConstraint("atom1_index >= 1", name="atom1_index_ge_1"),
        CheckConstraint("atom2_index >= 1", name="atom2_index_ge_1"),
        CheckConstraint(
            "atom3_index IS NULL OR atom3_index >= 1",
            name="atom3_index_ge_1",
        ),
        CheckConstraint(
            "atom4_index IS NULL OR atom4_index >= 1",
            name="atom4_index_ge_1",
        ),
        # Arity enforcement: bond=2, angle=3, dihedral/improper=4
        CheckConstraint(
            """
            CASE coordinate_kind
                WHEN 'bond' THEN atom3_index IS NULL AND atom4_index IS NULL
                WHEN 'angle' THEN atom3_index IS NOT NULL AND atom4_index IS NULL
                ELSE atom3_index IS NOT NULL AND atom4_index IS NOT NULL
            END
            """,
            name="coordinate_arity_matches_kind",
        ),
        CheckConstraint(
            "step_count IS NULL OR step_count >= 1",
            name="step_count_ge_1",
        ),
        CheckConstraint(
            "resolution_degrees IS NULL OR resolution_degrees >= 1",
            name="resolution_degrees_ge_1",
        ),
        CheckConstraint(
            "symmetry_number IS NULL OR symmetry_number >= 1",
            name="symmetry_number_ge_1",
        ),
    )


class CalculationConstraint(Base):
    """Geometric constraint applied to a calculation.

    Generalizes beyond scan-only constraints: supports constrained
    optimizations, TS searches, scans, and IRC setups.  Constraint
    kinds include internal coordinates (bond, angle, dihedral, improper)
    and Cartesian freezes (cartesian_atom).

    Arity by kind:
    - ``cartesian_atom``: 1 atom (atom2/3/4 = NULL)
    - ``bond``: 2 atoms
    - ``angle``: 3 atoms
    - ``dihedral``/``improper``: 4 atoms
    """

    __tablename__ = "calculation_constraint"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    constraint_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    constraint_kind: Mapped[ConstraintKind] = mapped_column(
        SAEnum(ConstraintKind, name="constraint_kind"),
        nullable=False,
    )
    atom1_index: Mapped[int] = mapped_column(Integer, nullable=False)
    atom2_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    atom3_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    atom4_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_value: Mapped[Optional[float]] = mapped_column(nullable=True)

    calculation: Mapped["Calculation"] = relationship(back_populates="constraints")

    __table_args__ = (
        CheckConstraint(
            "constraint_index >= 1",
            name="constraint_index_ge_1",
        ),
        CheckConstraint("atom1_index >= 1", name="atom1_index_ge_1"),
        CheckConstraint(
            "atom2_index IS NULL OR atom2_index >= 1",
            name="atom2_index_ge_1",
        ),
        CheckConstraint(
            "atom3_index IS NULL OR atom3_index >= 1",
            name="atom3_index_ge_1",
        ),
        CheckConstraint(
            "atom4_index IS NULL OR atom4_index >= 1",
            name="atom4_index_ge_1",
        ),
        # Arity enforcement by constraint kind
        CheckConstraint(
            """
            CASE constraint_kind
                WHEN 'cartesian_atom' THEN atom2_index IS NULL AND atom3_index IS NULL AND atom4_index IS NULL
                WHEN 'bond' THEN atom2_index IS NOT NULL AND atom3_index IS NULL AND atom4_index IS NULL
                WHEN 'angle' THEN atom2_index IS NOT NULL AND atom3_index IS NOT NULL AND atom4_index IS NULL
                ELSE atom2_index IS NOT NULL AND atom3_index IS NOT NULL AND atom4_index IS NOT NULL
            END
            """,
            name="constraint_arity_matches_kind",
        ),
    )


class CalculationScanPoint(Base):
    """One sampled point on a scan surface."""

    __tablename__ = "calc_scan_point"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    point_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    electronic_energy_hartree: Mapped[Optional[float]] = mapped_column(nullable=True)
    relative_energy_kj_mol: Mapped[Optional[float]] = mapped_column(nullable=True)
    geometry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(back_populates="scan_points")
    geometry: Mapped[Optional["Geometry"]] = relationship()
    coordinate_values: Mapped[list["CalculationScanPointCoordinateValue"]] = (
        relationship(
            back_populates="scan_point",
            cascade="all, delete-orphan",
            overlaps="point_coordinate_values,coordinate",
        )
    )

    __table_args__ = (CheckConstraint("point_index >= 1", name="point_index_ge_1"),)


class CalculationScanPointCoordinateValue(Base):
    """Coordinate values for one sampled scan point.

    ``coordinate_value``'s meaning is fixed by ADR 0020
    (``docs/adr/0020-a-scan-coordinate-value-is-the-coordinate-itself.md``),
    which supersedes ADR 0019's relative-axis reading. See the column
    comment below for the contract; ``app.services.scan_coordinate_conformance``
    is the read-time check that finds deposits that do not yet conform to it.
    """

    __tablename__ = "calc_scan_point_coordinate_value"

    calculation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    point_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    coordinate_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: The value of the internal coordinate *itself* at this sampled point,
    #: in that coordinate's own unit -- degrees for ``angle``/``dihedral``/
    #: ``improper``, Angstrom for ``bond`` (ADR 0020). Not a displacement
    #: and not relative to anything: ``calc_scan_coordinate.start_value``/
    #: ``end_value`` are the requested grid's extent (metadata, alongside
    #: ``step_count``/``step_size``), never an anchor added onto this
    #: column to recover the "real" value. ADR 0019 described the opposite
    #: convention -- a sweep relative to the first point, with the
    #: absolute value held in ``start_value`` -- and is SUPERSEDED.
    #:
    #: A periodic coordinate may continue past 360 degrees where doing so
    #: keeps a relaxed, path-dependent sweep monotone (419.867 and 59.867
    #: are the same physical angle; readers take ``mod 360``). Producers
    #: convert before depositing -- a program that prints a relative sweep
    #: necessarily holds the anchor it computed that sweep from, and
    #: applies it before upload. TCKDB does not accept the transformation
    #: and carry it forever, and does not adopt one producer's internal
    #: representation as the database's contract.
    #:
    #: The deposited corpus (46 series, all one-dimensional dihedral scans
    #: from a single producer) predates this decision and does not yet
    #: conform to it -- every one of those series holds ADR 0019's
    #: relative sweep. Correcting them in place is a separate, later
    #: migration; ``app.services.scan_coordinate_conformance`` is the
    #: read-time, warn-tier check (ADR 0008) that finds non-conforming
    #: deposits by recomputing this column from the point's own stored
    #: geometry, without reading ``start_value``/``end_value`` as an
    #: anchor.
    coordinate_value: Mapped[float] = mapped_column(nullable=False)
    value_unit: Mapped[Optional[CoordinateUnit]] = mapped_column(
        SAEnum(CoordinateUnit, name="coordinate_unit", create_type=False),
        nullable=True,
    )

    scan_point: Mapped["CalculationScanPoint"] = relationship(
        back_populates="coordinate_values",
        overlaps="point_coordinate_values,coordinate",
    )
    coordinate: Mapped["CalculationScanCoordinate"] = relationship(
        back_populates="point_coordinate_values",
        overlaps="coordinate_values,scan_point",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["calculation_id", "point_index"],
            ["calc_scan_point.calculation_id", "calc_scan_point.point_index"],
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_cspcv_calc_id_point_index",
        ),
        ForeignKeyConstraint(
            ["calculation_id", "coordinate_index"],
            [
                "calc_scan_coordinate.calculation_id",
                "calc_scan_coordinate.coordinate_index",
            ],
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_cspcv_calc_id_coordinate_index",
        ),
        CheckConstraint(
            "point_index >= 1",
            name="point_index_ge_1",
        ),
        CheckConstraint(
            "coordinate_index >= 1",
            name="coordinate_index_ge_1",
        ),
    )


class CalculationIRCResult(Base):
    """IRC-level metadata for an IRC calculation.

    Supports both single-direction (Gaussian: one log = one direction)
    and both-directions (ORCA: one log = forward + reverse) IRC runs.

    ``direction`` indicates the overall run mode:
    - ``forward`` / ``reverse`` for single-direction jobs
    - ``both`` for ORCA-style bidirectional IRC

    Per-point direction is on ``CalculationIRCPoint.direction``.
    """

    __tablename__ = "calc_irc_result"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    direction: Mapped[IRCDirection] = mapped_column(
        SAEnum(IRCDirection, name="irc_direction"),
        nullable=False,
    )
    has_forward: Mapped[bool] = mapped_column(default=False)
    has_reverse: Mapped[bool] = mapped_column(default=False)
    ts_point_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    point_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    zero_energy_reference_hartree: Mapped[Optional[float]] = mapped_column(
        nullable=True
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(back_populates="irc_result")
    points: Mapped[list["CalculationIRCPoint"]] = relationship(
        primaryjoin=(
            "CalculationIRCResult.calculation_id == "
            "foreign(CalculationIRCPoint.calculation_id)"
        ),
        viewonly=True,
        order_by="CalculationIRCPoint.point_index",
    )

    __table_args__ = (
        CheckConstraint(
            "point_count IS NULL OR point_count >= 0", name="point_count_ge_0"
        ),
    )


class CalculationIRCPoint(Base):
    """One sampled point on an IRC path.

    PK is ``(calculation_id, point_index)``.  ``point_index`` preserves
    the source step number from the log file.

    ``direction`` is set per-point to support both:
    - Gaussian (all points in one direction per log)
    - ORCA (both directions in one log, TS point has direction NULL)

    ``is_ts`` marks the transition-state point (ORCA ``<= TS`` marker,
    or Gaussian point 0).
    """

    __tablename__ = "calc_irc_point"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    point_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    direction: Mapped[Optional[IRCDirection]] = mapped_column(
        SAEnum(IRCDirection, name="irc_direction", create_type=False),
        nullable=True,
    )
    is_ts: Mapped[bool] = mapped_column(default=False)
    reaction_coordinate: Mapped[Optional[float]] = mapped_column(nullable=True)
    electronic_energy_hartree: Mapped[Optional[float]] = mapped_column(nullable=True)
    relative_energy_kj_mol: Mapped[Optional[float]] = mapped_column(nullable=True)
    max_gradient: Mapped[Optional[float]] = mapped_column(nullable=True)
    rms_gradient: Mapped[Optional[float]] = mapped_column(nullable=True)
    geometry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(back_populates="irc_points")
    geometry: Mapped[Optional["Geometry"]] = relationship()

    __table_args__ = (CheckConstraint("point_index >= 0", name="point_index_ge_0"),)


class CalculationPathSearchResult(Base):
    """Path-search-level metadata for a calculation that explored a
    reaction path between or from molecular endpoints.

    Generalizes path-based TS-search algorithms (NEB, GSM, growing/
    freezing string, ...). The specific algorithm is data on
    ``method`` rather than a separate ``CalculationType``. The path
    sample (images, nodes, ...) lives in ``calc_path_search_point``.
    """

    __tablename__ = "calc_path_search_result"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    method: Mapped[PathSearchMethod] = mapped_column(
        SAEnum(PathSearchMethod, name="path_search_method"),
        nullable=False,
    )
    is_double_ended: Mapped[Optional[bool]] = mapped_column(nullable=True)
    converged: Mapped[Optional[bool]] = mapped_column(nullable=True)
    n_points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    selected_ts_point_index: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    climbing_image_index: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    source_endpoint_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    zero_energy_reference_hartree: Mapped[Optional[float]] = mapped_column(
        nullable=True
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(
        back_populates="path_search_result"
    )
    points: Mapped[list["CalculationPathSearchPoint"]] = relationship(
        primaryjoin=(
            "CalculationPathSearchResult.calculation_id == "
            "foreign(CalculationPathSearchPoint.calculation_id)"
        ),
        viewonly=True,
        order_by="CalculationPathSearchPoint.point_index",
    )

    __table_args__ = (
        CheckConstraint(
            "n_points IS NULL OR n_points >= 1", name="n_points_ge_1"
        ),
        CheckConstraint(
            "selected_ts_point_index IS NULL OR selected_ts_point_index >= 0",
            name="selected_ts_point_index_ge_0",
        ),
        CheckConstraint(
            "climbing_image_index IS NULL OR climbing_image_index >= 0",
            name="climbing_image_index_ge_0",
        ),
        CheckConstraint(
            "source_endpoint_count IS NULL OR source_endpoint_count >= 1",
            name="source_endpoint_count_ge_1",
        ),
    )


class CalculationPathSearchPoint(Base):
    """One sampled point on a path-search calculation's reaction path.

    Generalizes NEB images, GSM nodes, and string-method path points.
    PK is ``(calculation_id, point_index)``. ``point_index`` preserves
    the source ordering from the algorithm (0 = reactant endpoint for
    double-ended methods).
    """

    __tablename__ = "calc_path_search_point"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    point_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    electronic_energy_hartree: Mapped[Optional[float]] = mapped_column(nullable=True)
    relative_energy_kj_mol: Mapped[Optional[float]] = mapped_column(nullable=True)
    path_coordinate: Mapped[Optional[float]] = mapped_column(nullable=True)
    max_force: Mapped[Optional[float]] = mapped_column(nullable=True)
    rms_force: Mapped[Optional[float]] = mapped_column(nullable=True)
    max_gradient: Mapped[Optional[float]] = mapped_column(nullable=True)
    rms_gradient: Mapped[Optional[float]] = mapped_column(nullable=True)
    is_ts_guess: Mapped[bool] = mapped_column(default=False)
    is_climbing_image: Mapped[bool] = mapped_column(default=False)
    geometry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(
        back_populates="path_search_points"
    )
    geometry: Mapped[Optional["Geometry"]] = relationship()

    __table_args__ = (
        CheckConstraint("point_index >= 0", name="point_index_ge_0"),
    )


class CalculationArtifact(Base, TimestampMixin, CreatedByMixin):
    """Append-only artifact metadata: bytes-on-S3 plus minimal upload context.

    Each row records ONE upload event for ONE file attached to ONE
    calculation. Rows are intentionally append-only — duplicate uploads
    of the same content (same sha256) produce two rows pointing at the
    same content-addressed object. The row carries the original
    ``filename`` and uploading ``created_by`` so the audit trail is
    meaningful even when the bytes alone are opaque (e.g. binary
    checkpoints).

    Note: ``checkpoint`` and ``formatted_checkpoint`` are supported
    artifact kinds, but they are opt-in and expensive. Producers (e.g.
    ARC) should default to ``output_log`` only. Checkpoint-class
    artifacts are mainly useful for curated reanalysis, restart/debug
    scenarios, or exact binary audit trails — not routine bulk upload.
    """

    __tablename__ = "calculation_artifact"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    calculation_id: Mapped[int] = mapped_column(
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[ArtifactKind] = mapped_column(
        SAEnum(ArtifactKind, name="artifact_kind"),
        nullable=False,
        index=True,
    )
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False, index=True)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # created_by from CreatedByMixin (nullable FK to app_user.id)
    # created_at from TimestampMixin

    calculation: Mapped["Calculation"] = relationship(back_populates="artifacts")

    #: Recorded custody breaks for the *object* this row points at.
    #:
    #: Joined on ``sha256``, not on a foreign key, and that is the whole
    #: point. The store is content-addressed: N artifact rows across N
    #: calculations may share one object, and if that object's bytes stop
    #: matching their digest then every one of those rows is affected —
    #: not just the row whose download happened to discover it. A
    #: per-row FK would let a corrupt object condemn one record and leave
    #: its N-1 twins reading as sound.
    #:
    #: ``lazy="selectin"`` rather than a loader option at each call site.
    #: Eleven read paths already ``selectinload(Calculation.artifacts)`` on
    #: the way to a trust evaluation, and a custody break that is only
    #: visible to the paths someone remembered to annotate is the same
    #: failure this whole change exists to end. The price is one extra
    #: indexed ``sha256 IN (...)`` per artifact-loading statement, which
    #: returns nothing in the normal case; verifying custody is not free
    #: and this is the bounded, constant version of the bill.
    #:
    #: ``order_by`` is ``id`` because the trust evaluator reads the last
    #: element as *the latest observation*, and that definition is owned
    #: by
    #: :func:`app.services.artifact_integrity.latest_integrity_observations`.
    #: This relationship exists rather than a call to the owner because
    #: the evaluator has no session -- it grades already-loaded rows, and
    #: making a hard-fail decision depend on a query would put custody
    #: back behind a loader option someone has to remember. The two are
    #: held equal by an equivalence test over a generated population
    #: (``tests/services/test_artifact_integrity.py``), not by this note.
    integrity_events: Mapped[list["ArtifactIntegrityEvent"]] = relationship(
        primaryjoin=lambda: foreign(ArtifactIntegrityEvent.sha256)
        == CalculationArtifact.sha256,
        viewonly=True,
        lazy="selectin",
        order_by="ArtifactIntegrityEvent.id",
    )

    __table_args__ = (
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_lower_hex",
        ),
        CheckConstraint("bytes > 0", name="bytes_gt_0"),
    )


class ArtifactIntegrityEvent(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """One observation about TCKDB's custody of a stored artifact.

    Append-only. A row here says: *at this moment, the bytes behind this
    content-addressed digest were / were not the bytes TCKDB claims to
    hold.* A break is not an HTTP incident and not a transient error — it
    is corruption of stored evidence, or a swapped object, and TCKDB
    refuses payloads for less. A log line is not a record; this table is.

    Nothing here is ever updated or deleted, including on repair. An
    object restored to its correct bytes is a **new** observation
    (``finding='verified'``) that supersedes the older break, and a check
    constraint requires it to carry a digest matching the key — so a hard
    fail can be cleared by evidence and never by assertion. The trust
    evaluator reads the *latest* observation per digest, which is what
    keeps the label a judgement rather than a trap.

    Keyed by digest, not by row
    ---------------------------
    ``sha256`` is NOT NULL and is the primary handle; ``artifact_id`` is
    nullable and merely names the row that led us to look. In a
    content-addressed store the unit of corruption is the *object*, and
    an object may be shared by many ``calculation_artifact`` rows or —
    on the ``store_artifact`` dedup path — by none yet, because the row
    that would have referenced it is being refused. Both cases have to be
    recordable.

    Telling the three causes apart
    ------------------------------
    A digest mismatch has three distinct causes with three different
    remedies, and the row captures enough to separate them without a
    second investigation:

    ==================================================  ==========================================
    Evidence in the row                                  Reading
    ==================================================  ==========================================
    ``object_last_modified_at`` materially later than    The object was **modified after write**.
    ``artifact_recorded_at``                             Look for a writer with bucket
                                                         credentials; the ingest was sound.
    ``object_last_modified_at`` at or before             We **never stored what we said we did**.
    ``artifact_recorded_at``, and ``object_etag``        The digest was computed over different
    consistent with the bytes read                       bytes than were PUT. Look at the ingest
                                                         path, not the store.
    ``object_etag`` inconsistent with the bytes read,    The **store returned wrong bytes** on this
    or ``object_content_length`` disagreeing with        read. The object may still be sound;
    ``observed_bytes``                                   re-read before condemning it.
    ==================================================  ==========================================

    ``artifact_recorded_at`` is a *copy* of the artifact row's
    ``created_at`` taken at detection rather than a join, so the
    comparison above survives the artifact row being removed and does not
    depend on a correspondence this table exists to doubt.

    Consequence
    -----------
    The existence of a row here is what
    :class:`~app.services.trust.models.HardFailReason.artifact_integrity_failed`
    reads. Detection therefore has a read-time consequence for *every*
    reader of the owning calculation, not only for whoever requested the
    download. See ``docs/adr/0014-custody-of-stored-evidence.md``.

    Citable
    -------
    ``public_ref`` (``aie_``) exists because the consequence is cited.
    The reproducibility rubric copies this record's verdict for artifacts
    it does not read itself and names the observation it copied, so a
    curator holding that citation has to be able to resolve it -- and the
    citation was a row id, which is not TCKDB's to hand out and which the
    read surface strips by policy. Opaque rather than content-derived: an
    observation is an event, and two identical-looking observations of
    the same object months apart are the whole point of an append-only
    log.
    """

    __tablename__ = "artifact_integrity_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    #: The content-addressed object whose custody broke. The handle.
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False, index=True)

    #: The row that led us to read the object, when there was one.
    #: NULL on the ``store_dedup_verification`` path, where the upload
    #: whose row would have referenced it is being refused.
    artifact_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("calculation_artifact.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )

    finding: Mapped[ArtifactIntegrityFinding] = mapped_column(
        SAEnum(ArtifactIntegrityFinding, name="artifact_integrity_finding"),
        nullable=False,
    )
    detected_during: Mapped[ArtifactIntegrityDetectionContext] = mapped_column(
        SAEnum(
            ArtifactIntegrityDetectionContext,
            name="artifact_integrity_detection_context",
        ),
        nullable=False,
    )

    #: What the database said the object should be, copied at detection.
    expected_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    #: What was actually read back. NULL when the object was absent.
    observed_sha256: Mapped[Optional[str]] = mapped_column(CHAR(64), nullable=True)
    observed_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    #: Object metadata as the store reported it at detection (HEAD), best
    #: effort. These three columns are the cause discriminators above; a
    #: store that cannot be reached for metadata leaves them NULL and the
    #: row still records that the failure happened.
    object_last_modified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    object_etag: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    object_content_length: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )

    #: Copy of ``calculation_artifact.created_at`` — when TCKDB says it
    #: took custody. Compared against ``object_last_modified_at``.
    artifact_recorded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    #: The verifier's own message. Prose, for the operator; every
    #: machine-readable fact is in a column above.
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # created_by from CreatedByMixin — the reader who hit it, when a
    # request actor is in scope. NULL for the sweep.
    # created_at from TimestampMixin — when it was detected.

    artifact: Mapped[Optional["CalculationArtifact"]] = relationship(
        foreign_keys=[artifact_id],
    )

    __table_args__ = (
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_lower_hex",
        ),
        CheckConstraint(
            "observed_sha256 IS NULL OR observed_sha256 ~ '^[0-9a-f]{64}$'",
            name="observed_sha256_lower_hex",
        ),
        # An object that is missing cannot have been read, and an object
        # whose digest mismatched must say what it read instead —
        # otherwise the row records an alarm without its evidence.
        CheckConstraint(
            "(finding = 'object_missing' AND observed_sha256 IS NULL) "
            "OR (finding <> 'object_missing' AND observed_sha256 IS NOT NULL)",
            name="observed_digest_present_iff_read",
        ),
        # A clearing observation has to carry its own proof. Recording
        # ``verified`` without the matching digest would let an operator
        # clear a hard fail by assertion, which is precisely the move
        # this table exists to make impossible.
        CheckConstraint(
            "finding <> 'verified' OR observed_sha256 = sha256",
            name="verified_requires_matching_digest",
        ),
        Index(
            "ix_artifact_integrity_event_sha256_created_at",
            "sha256",
            "created_at",
        ),
    )


class CalculationParameterVocab(Base, TimestampMixin):
    """Ontology seed for canonical parameter keys.

    Keyed by canonical_key (not a surrogate ID) — the key itself is the
    stable semantic handle.  Classification flags enable filtering: e.g.
    ``affects_scientific_result = true`` selects only parameters that matter
    for "same setup" comparisons.
    """

    __tablename__ = "calculation_parameter_vocab"

    canonical_key: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_value_type: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Expected type: bool, int, float, string, enum"
    )
    affects_scientific_result: Mapped[Optional[bool]] = mapped_column(
        nullable=True,
        doc="Can materially affect the scientific result or comparability",
    )
    affects_numerics: Mapped[Optional[bool]] = mapped_column(
        nullable=True,
        doc="Affects numerical precision / convergence behaviour",
    )
    affects_resources: Mapped[Optional[bool]] = mapped_column(
        nullable=True,
        doc="Operational / resource / bookkeeping only",
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    parameters: Mapped[list["CalculationParameter"]] = relationship(
        back_populates="vocab",
    )


class CalculationParameter(Base, TimestampMixin):
    """EAV-style parsed parameter from an ESS calculation.

    Stores both raw (software-specific) and canonical (normalized) key/value
    pairs.  Software identity is derived via calculation → software_release → software,
    not duplicated here.
    """

    __tablename__ = "calculation_parameter"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    calculation_id: Mapped[int] = mapped_column(
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    raw_key: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_key: Mapped[Optional[str]] = mapped_column(
        ForeignKey(
            "calculation_parameter_vocab.canonical_key",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_calculation_parameter_canonical_key",
        ),
        nullable=True,
    )
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    section: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Route-line section: opt, scf, integral, grid, resource"
    )
    value_type: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Hint for consumers: bool, int, float, string, enum"
    )
    unit: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parameter_index: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="Ordering for repeated/positional options"
    )
    source: Mapped[ParameterSource] = mapped_column(
        SAEnum(ParameterSource, name="calculation_parameter_source"),
        nullable=False,
        default=ParameterSource.upload,
        server_default=ParameterSource.upload.value,
        doc="Row provenance: parser-extracted, upload-supplied, or curated.",
    )
    parser_version: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Parser version that produced this row, when source='parser'.",
    )

    calculation: Mapped["Calculation"] = relationship(back_populates="parameters")
    vocab: Mapped[Optional["CalculationParameterVocab"]] = relationship(
        back_populates="parameters",
    )

    __table_args__ = (
        Index("ix_calculation_parameter_calculation_id", "calculation_id"),
        Index("ix_calculation_parameter_canonical_key", "canonical_key"),
        Index(
            "ix_calculation_parameter_raw_key_section",
            "raw_key",
            "section",
        ),
        Index(
            "ix_calculation_parameter_canonical_key_value",
            "canonical_key",
            "canonical_value",
        ),
        Index(
            "ix_calculation_parameter_source",
            "calculation_id",
            "source",
        ),
        CheckConstraint(
            "parameter_index IS NULL OR parameter_index >= 0",
            name="parameter_index_ge_0",
        ),
    )


class CalculationGeometryValidation(Base, TimestampMixin):
    """Evidence that a calculation's output geometry preserves the intended molecular identity.

    This is a *structure-consistency* check: it compares the calculation's
    output geometry (and optionally its input geometry) against the declared
    species identity, using graph isomorphism as the identity criterion and
    Kabsch-aligned RMSD as a suspicion signal. It is intended to catch cases
    where an optimization rearranged the molecule, broke or formed bonds,
    dissociated the species, transferred a proton, or otherwise produced a
    different chemical identity than the one being claimed.

    What this is NOT:

    * **Not SCF / wavefunction stability.** Whether the electronic
      wavefunction is stable with respect to orbital rotations
      (Gaussian ``Stable`` / ``Stable=Opt``, ORCA stability analysis) lives
      in :class:`CalculationSCFStability` (``calc_scf_stability``). That is
      an electronic-structure check, not a geometry/identity check.
    * **Not frequency / stationary-point validation.** Whether the geometry
      is a minimum vs. a saddle (number of imaginary frequencies, Hessian
      character) lives on the frequency result tables, not here.

    One row per calculation (PK = ``calculation_id``). Absence of a row means
    geometry validation was not performed; it does not mean the geometry is
    invalid. The record-producing service is
    :func:`app.services.geometry_validation.validate_calculation_geometry`,
    wired into the computed-species and computed-reaction bundle workflows
    for species-side opt calcs (TS opt is intentionally deferred to a
    future reaction-aware validator).

    **Interpreting a ``fail`` row.** A ``validation_status=fail`` row means
    "the automated identity validator found a mismatch," **not** "the
    calculation is scientifically invalid." Connectivity perception from
    XYZ is imperfect for weak complexes, stretched or partially broken
    bonds, radicals, charged species, loose conformers, and
    proton-transfer-like geometries — all of which can legitimately
    produce false-positive ``fail`` rows even when the underlying
    calculation is fine. These rows are intended as *curator attention*
    signals, not as inputs to automatic rejection or quality gating.
    """

    __tablename__ = "calc_geometry_validation"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    input_geometry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    output_geometry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    species_smiles: Mapped[str] = mapped_column(Text, nullable=False)
    is_isomorphic: Mapped[bool] = mapped_column(nullable=False)
    rmsd: Mapped[Optional[float]] = mapped_column(nullable=True)
    atom_mapping: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    n_mappings: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    validation_status: Mapped[ValidationStatus] = mapped_column(
        SAEnum(ValidationStatus, name="validation_status"),
        nullable=False,
    )
    validation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rmsd_warning_threshold: Mapped[Optional[float]] = mapped_column(nullable=True)

    calculation: Mapped["Calculation"] = relationship(
        back_populates="geometry_validation"
    )
    input_geometry: Mapped[Optional["Geometry"]] = relationship(
        foreign_keys=[input_geometry_id],
    )
    output_geometry: Mapped[Optional["Geometry"]] = relationship(
        foreign_keys=[output_geometry_id],
    )


class CalculationSCFStability(Base, TimestampMixin, CreatedByMixin):
    """SCF wavefunction stability evidence for a calculation.

    A row exists only when a stability analysis was actually attempted
    by the producer. Absence of a row means "not checked" — read APIs
    project this as :attr:`SCFStabilityStatus` with no stored value;
    no row is inserted to represent ``not_checked``.

    Producer contract (not enforced by DB constraint, deliberately):

    * Emit ``status = stable`` only when an SCF/wavefunction stability
      analysis was observed. Ordinary SCF convergence is NOT enough.
    * If unsure whether a stability analysis was performed, omit the
      block entirely so the read API projects ``not_checked``.
    * Use ``status = inconclusive`` only when a stability analysis was
      clearly attempted but its result could not be parsed.
    """

    __tablename__ = "calc_scf_stability"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    status: Mapped[SCFStabilityStatus] = mapped_column(
        SAEnum(SCFStabilityStatus, name="scf_stability_status"),
        nullable=False,
    )
    lowest_eigenvalue: Mapped[Optional[float]] = mapped_column(nullable=True)
    instability_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    instability_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reoptimized_wavefunction: Mapped[Optional[bool]] = mapped_column(nullable=True)
    source_calculation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    source_artifact_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "calculation_artifact.id", deferrable=True, initially="IMMEDIATE"
        ),
        nullable=True,
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(
        back_populates="scf_stability",
        foreign_keys=[calculation_id],
    )
    source_calculation: Mapped[Optional["Calculation"]] = relationship(
        foreign_keys=[source_calculation_id],
    )
    source_artifact: Mapped[Optional["CalculationArtifact"]] = relationship(
        foreign_keys=[source_artifact_id],
    )

    __table_args__ = (
        CheckConstraint(
            "instability_count IS NULL OR instability_count >= 0",
            name="instability_count_ge_0",
        ),
        CheckConstraint(
            "NOT (status = 'stable' AND reoptimized_wavefunction IS TRUE)",
            name="stable_no_reopt",
        ),
        CheckConstraint(
            "NOT (status = 'stabilized' AND instability_count = 0)",
            name="stabilized_has_instability",
        ),
    )


class CalculationHessian(Base, TimestampMixin, CreatedByMixin):
    """Cartesian second-derivative (Hessian) matrix for a calculation.

    A one-row-per-calculation side table (absent by default, like
    :class:`CalculationSCFStability`). Stores the packed lower triangle
    (including the diagonal) of the symmetric 3N×3N Cartesian
    force-constant matrix, row-major, in fixed units of hartree/bohr².
    The matrix is meaningless without its atomic configuration, ordering,
    and orientation, so ``geometry_id`` is mandatory: it binds the Hessian
    to the exact :class:`~app.db.models.geometry.Geometry` it was computed
    at (deduped through the content-addressed geometry seam, so it usually
    coincides with the calculation's input geometry with no duplication).

    See DR-0030 for the full rationale.
    """

    __tablename__ = "calc_hessian"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    geometry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    natoms: Mapped[int] = mapped_column(Integer, nullable=False)
    lower_triangle_hartree_bohr2: Mapped[list[float]] = mapped_column(
        ARRAY(Float), nullable=False
    )
    source: Mapped[HessianSource] = mapped_column(
        SAEnum(HessianSource, name="hessian_source"),
        nullable=False,
    )
    parser_version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(
        back_populates="hessian",
        foreign_keys=[calculation_id],
    )
    geometry: Mapped["Geometry"] = relationship(foreign_keys=[geometry_id])

    __table_args__ = (
        CheckConstraint("natoms >= 1", name="hessian_natoms_ge_1"),
        # Packed lower triangle (with diagonal) of a symmetric 3N×3N matrix
        # has exactly 3N(3N+1)/2 entries.
        CheckConstraint(
            "cardinality(lower_triangle_hartree_bohr2) "
            "= (3 * natoms) * (3 * natoms + 1) / 2",
            name="hessian_lower_triangle_cardinality",
        ),
    )


class CalculationWavefunctionDiagnostic(Base, TimestampMixin, CreatedByMixin):
    """Parsed coupled-cluster / multireference diagnostics for a calculation.

    Carries scalar diagnostics emitted by the ESS at parse time — T1
    (Lee–Taylor), D1 (Janowski), the norm of the T1 amplitude vector,
    and the largest T2 amplitude. The row is producer-supplied evidence
    about the reliability of the electronic-structure result; it is
    deliberately not interpreted by the schema (no thresholds, no
    "good/bad" labels) — readers and curators apply heuristics on top.

    Producer contract (not enforced by DB):

    * Emit a row only when at least one diagnostic was actually parsed
      from the calculation output. Absence of a row reads as "not parsed
      / not applicable / not reported" — there is no ``not_checked``
      enum.
    * Spin-contamination signals (``<S^2>``) are NOT carried here; they
      will land in a separate diagnostic table once their schema is
      reviewed.
    """

    __tablename__ = "calc_wavefunction_diagnostic"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    t1_diagnostic: Mapped[Optional[float]] = mapped_column(nullable=True)
    d1_diagnostic: Mapped[Optional[float]] = mapped_column(nullable=True)
    t1_norm: Mapped[Optional[float]] = mapped_column(nullable=True)
    largest_t2_amplitude: Mapped[Optional[float]] = mapped_column(nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(
        back_populates="wavefunction_diagnostic",
    )

    __table_args__ = (
        CheckConstraint(
            "t1_diagnostic IS NULL OR t1_diagnostic >= 0",
            name="t1_diagnostic_ge_0",
        ),
        CheckConstraint(
            "d1_diagnostic IS NULL OR d1_diagnostic >= 0",
            name="d1_diagnostic_ge_0",
        ),
        CheckConstraint(
            "t1_norm IS NULL OR t1_norm >= 0",
            name="t1_norm_ge_0",
        ),
        CheckConstraint(
            "largest_t2_amplitude IS NULL OR largest_t2_amplitude >= 0",
            name="largest_t2_amplitude_ge_0",
        ),
    )


class CalculationSpinDiagnostic(Base, TimestampMixin, CreatedByMixin):
    """Parsed spin-contamination ``<S^2>`` evidence for a calculation.

    The companion to :class:`CalculationWavefunctionDiagnostic`: where that
    table carries coupled-cluster / multireference scalars, this one carries
    the spin-contamination signals an ESS reports for an *unrestricted*
    calculation — the observed ``<S^2>``, the ideal ``S(S+1)`` for the target
    spin state, and (when the software prints it) the ``<S^2>`` after
    annihilation of the first spin contaminant. The row is producer-supplied
    evidence about the quality of the electronic-structure result; it is
    deliberately not interpreted by the schema (no thresholds, no "clean /
    contaminated" labels) — readers and curators apply heuristics on top.

    Wavefunction diagnostics (T1/D1) said their ``<S^2>`` signals "will land
    in a separate diagnostic table"; this is that table.

    Producer contract (not enforced by DB):

    * Emit a row only when ``<S^2>`` was actually parsed from the calculation
      output. Absence of a row reads as "not parsed / not applicable / not
      reported" — there is no ``not_checked`` sentinel.
    * Applies to any UNRESTRICTED calculation (not just coupled cluster);
      restricted (closed-shell / RO) runs have no ``<S^2>`` contamination to
      report and should omit the block.
    """

    __tablename__ = "calc_spin_diagnostic"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    s_squared: Mapped[float] = mapped_column(nullable=False)
    s_squared_expected: Mapped[Optional[float]] = mapped_column(nullable=True)
    s_squared_annihilated: Mapped[Optional[float]] = mapped_column(nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    calculation: Mapped["Calculation"] = relationship(
        back_populates="spin_diagnostic",
    )

    __table_args__ = (
        CheckConstraint(
            "s_squared >= 0",
            name="s_squared_ge_0",
        ),
        CheckConstraint(
            "s_squared_expected IS NULL OR s_squared_expected >= 0",
            name="s_squared_expected_ge_0",
        ),
        CheckConstraint(
            "s_squared_annihilated IS NULL OR s_squared_annihilated >= 0",
            name="s_squared_annihilated_ge_0",
        ),
    )
