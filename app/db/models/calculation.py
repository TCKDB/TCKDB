from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConstraintKind,
    CoordinateUnit,
    IRCDirection,
    ScanCoordinateKind,
    ValidationStatus,
)

if TYPE_CHECKING:
    from app.db.models.geometry import Geometry
    from app.db.models.level_of_theory import LevelOfTheory
    from app.db.models.literature import Literature
    from app.db.models.software import SoftwareRelease
    from app.db.models.species import ConformerObservation, SpeciesEntry
    from app.db.models.transition_state import TransitionStateEntry
    from app.db.models.workflow import WorkflowToolRelease


class Calculation(Base, TimestampMixin, CreatedByMixin):
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
    )
    transition_state_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("transition_state_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    software_release_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("software_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("workflow_tool_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    lot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("level_of_theory.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    literature_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    conformer_observation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "conformer_observation.id", deferrable=True, initially="IMMEDIATE"
        ),
        nullable=True,
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
    neb_images: Mapped[list["CalculationNEBImageResult"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
        order_by="CalculationNEBImageResult.image_index",
    )
    artifacts: Mapped[list["CalculationArtifact"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
    )
    parameters: Mapped[list["CalculationParameter"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
    )
    geometry_validation: Mapped[Optional["CalculationGeometryValidation"]] = relationship(
        back_populates="calculation",
        cascade="all, delete-orphan",
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
    `single_point_on`, `scan_parent`, and `neb_parent`.
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
        Index(
            "uq_calculation_dependency_child_calculation_id_neb_parent",
            "child_calculation_id",
            unique=True,
            postgresql_where=text("dependency_role = 'neb_parent'"),
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

    calculation: Mapped["Calculation"] = relationship(back_populates="freq_result")


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
    """Coordinate values for one sampled scan point."""

    __tablename__ = "calc_scan_point_coordinate_value"

    calculation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    point_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    coordinate_index: Mapped[int] = mapped_column(Integer, primary_key=True)
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
        ),
        ForeignKeyConstraint(
            ["calculation_id", "coordinate_index"],
            [
                "calc_scan_coordinate.calculation_id",
                "calc_scan_coordinate.coordinate_index",
            ],
            deferrable=True,
            initially="IMMEDIATE",
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


class CalculationNEBImageResult(Base):
    """Per-image result from a NEB calculation.

    Stores the converged energy profile along the NEB path.
    PK is ``(calculation_id, image_index)``.  Image 0 is the reactant
    endpoint, image N is the product endpoint.

    The ``is_climbing_image`` flag marks the image that was promoted to
    a climbing image in NEB-CI — typically the highest-energy image,
    which approximates the transition state.
    """

    __tablename__ = "calc_neb_image_result"

    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    image_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    electronic_energy_hartree: Mapped[Optional[float]] = mapped_column(nullable=True)
    relative_energy_kj_mol: Mapped[Optional[float]] = mapped_column(nullable=True)
    path_distance_angstrom: Mapped[Optional[float]] = mapped_column(nullable=True)
    max_force: Mapped[Optional[float]] = mapped_column(nullable=True)
    rms_force: Mapped[Optional[float]] = mapped_column(nullable=True)
    is_climbing_image: Mapped[bool] = mapped_column(default=False)

    calculation: Mapped["Calculation"] = relationship(back_populates="neb_images")

    __table_args__ = (
        CheckConstraint("image_index >= 0", name="image_index_ge_0"),
    )


class CalculationArtifact(Base, TimestampMixin):
    __tablename__ = "calculation_artifact"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    calculation_id: Mapped[int] = mapped_column(
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    kind: Mapped[ArtifactKind] = mapped_column(
        SAEnum(ArtifactKind, name="artifact_kind"),
        nullable=False,
    )
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(CHAR(64), nullable=True)
    bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    calculation: Mapped["Calculation"] = relationship(back_populates="artifacts")


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
        CheckConstraint(
            "parameter_index IS NULL OR parameter_index >= 0",
            name="parameter_index_ge_0",
        ),
    )


class CalculationGeometryValidation(Base, TimestampMixin):
    """Result of validating a calculation's output geometry against species identity.

    Stores graph isomorphism check, Kabsch-aligned RMSD, and the policy decision.
    One row per calculation (PK = calculation_id).
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
