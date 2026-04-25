from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConstraintKind,
    CoordinateUnit,
    IRCDirection,
    ValidationStatus,
)
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
    TimestampedReadSchema,
)
from app.schemas.entities.geometry import GeometryRead
from app.schemas.fragments.calculation import CalculationOwnerRequiredMixin


class CalculationCreateResolved(CalculationOwnerRequiredMixin, SchemaBase):
    """Internal calculation payload after scientific references are resolved to ids."""

    type: CalculationType
    quality: CalculationQuality = CalculationQuality.raw

    species_entry_id: int | None = None
    transition_state_entry_id: int | None = None

    software_release_id: int
    workflow_tool_release_id: int | None = None
    lot_id: int

    literature_id: int | None = None


class CalculationUpdate(SchemaBase):
    type: CalculationType | None = None
    quality: CalculationQuality | None = None

    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None
    lot_id: int | None = None

    literature_id: int | None = None


class CalculationRead(TimestampedReadSchema):
    type: CalculationType
    quality: CalculationQuality

    species_entry_id: int | None = None
    transition_state_entry_id: int | None = None
    conformer_observation_id: int | None = None

    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None
    lot_id: int | None = None

    literature_id: int | None = None


class CalculationInputGeometryBase(BaseModel):
    calculation_id: int
    geometry_id: int
    input_order: int = Field(default=1, ge=1)


class CalculationInputGeometryCreate(CalculationInputGeometryBase, SchemaBase):
    pass


class CalculationInputGeometryUpdate(SchemaBase):
    geometry_id: int | None = None
    input_order: int | None = Field(default=None, ge=1)


class CalculationInputGeometryRead(CalculationInputGeometryBase, ORMBaseSchema):
    pass


class CalculationOutputGeometryBase(BaseModel):
    calculation_id: int
    geometry_id: int
    output_order: int = Field(default=1, ge=1)
    role: CalculationGeometryRole | None = None


class CalculationOutputGeometryCreate(CalculationOutputGeometryBase, SchemaBase):
    pass


class CalculationOutputGeometryUpdate(SchemaBase):
    geometry_id: int | None = None
    output_order: int | None = Field(default=None, ge=1)
    role: CalculationGeometryRole | None = None


class CalculationOutputGeometryRead(CalculationOutputGeometryBase, ORMBaseSchema):
    pass


class CalculationInputGeometryDetailRead(ORMBaseSchema):
    """Geometry link with embedded geometry payload for input-geometry sub-resource."""

    geometry_id: int
    input_order: int
    geometry: GeometryRead


class CalculationOutputGeometryDetailRead(ORMBaseSchema):
    """Geometry link with embedded geometry payload for output-geometry sub-resource."""

    geometry_id: int
    output_order: int
    role: CalculationGeometryRole | None = None
    geometry: GeometryRead


class CalculationDependencyBase(BaseModel):
    parent_calculation_id: int
    child_calculation_id: int
    dependency_role: CalculationDependencyRole

    @model_validator(mode="after")
    def validate_not_self_edge(self) -> Self:
        if self.parent_calculation_id == self.child_calculation_id:
            raise ValueError("Calculation dependencies cannot be self-edges")
        return self


class CalculationDependencyCreate(CalculationDependencyBase, SchemaBase):
    pass


class CalculationDependencyUpdate(SchemaBase):
    """Patch schema for routes that already identify the dependency edge by PK."""

    dependency_role: CalculationDependencyRole | None = None


class CalculationDependencyRead(CalculationDependencyBase, ORMBaseSchema):
    pass


class CalculationDependencyDirectionalRead(CalculationDependencyBase, ORMBaseSchema):
    """Dependency edge annotated with direction relative to the queried calculation."""

    direction: Literal["outgoing", "incoming"]


class CalculationArtifactBase(BaseModel):
    calculation_id: int
    kind: ArtifactKind
    uri: str = Field(min_length=1)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    bytes: int | None = Field(default=None, ge=0)


class CalculationArtifactCreate(CalculationArtifactBase, SchemaBase):
    pass


class CalculationArtifactUpdate(SchemaBase):
    kind: ArtifactKind | None = None
    uri: str | None = Field(default=None, min_length=1)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    bytes: int | None = Field(default=None, ge=0)


class CalculationArtifactRead(CalculationArtifactBase, TimestampedReadSchema):
    pass


class CalculationSPResultBase(BaseModel):
    calculation_id: int
    electronic_energy_hartree: float | None = None
    electronic_energy_uncertainty_hartree: float | None = None


class CalculationSPResultCreate(CalculationSPResultBase, SchemaBase):
    pass


class CalculationSPResultUpdate(SchemaBase):
    electronic_energy_hartree: float | None = None
    electronic_energy_uncertainty_hartree: float | None = None


class CalculationSPResultRead(CalculationSPResultBase, ORMBaseSchema):
    pass


class CalculationOptResultBase(BaseModel):
    calculation_id: int
    converged: bool | None = None
    n_steps: int | None = Field(default=None, ge=0)
    final_energy_hartree: float | None = None


class CalculationOptResultCreate(CalculationOptResultBase, SchemaBase):
    pass


class CalculationOptResultUpdate(SchemaBase):
    converged: bool | None = None
    n_steps: int | None = Field(default=None, ge=0)
    final_energy_hartree: float | None = None


class CalculationOptResultRead(CalculationOptResultBase, ORMBaseSchema):
    pass


class CalculationFreqResultBase(BaseModel):
    calculation_id: int
    n_imag: int | None = None
    imag_freq_cm1: float | None = None
    zpe_hartree: float | None = None
    zpe_uncertainty_hartree: float | None = None


class CalculationFreqResultCreate(CalculationFreqResultBase, SchemaBase):
    pass


class CalculationFreqResultUpdate(SchemaBase):
    n_imag: int | None = None
    imag_freq_cm1: float | None = None
    zpe_hartree: float | None = None
    zpe_uncertainty_hartree: float | None = None


class CalculationFreqResultRead(CalculationFreqResultBase, ORMBaseSchema):
    pass


class CalculationScanCoordinatePayload(BaseModel):
    coordinate_index: int = Field(ge=1)
    coordinate_kind: str = Field(pattern=r"^(bond|angle|dihedral|improper)$")
    atom1_index: int = Field(ge=1)
    atom2_index: int = Field(ge=1)
    atom3_index: int | None = Field(default=None, ge=1)
    atom4_index: int | None = Field(default=None, ge=1)
    step_count: int | None = Field(default=None, ge=1)
    step_size: float | None = None
    start_value: float | None = None
    end_value: float | None = None
    value_unit: CoordinateUnit | None = None
    resolution_degrees: float | None = None
    symmetry_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_arity_and_distinct_atoms(self) -> Self:
        atoms = [self.atom1_index, self.atom2_index]
        if self.atom3_index is not None:
            atoms.append(self.atom3_index)
        if self.atom4_index is not None:
            atoms.append(self.atom4_index)

        expected = {"bond": 2, "angle": 3, "dihedral": 4, "improper": 4}
        n_expected = expected[self.coordinate_kind]
        if len(atoms) != n_expected:
            raise ValueError(
                f"{self.coordinate_kind} coordinate requires {n_expected} atoms, "
                f"got {len(atoms)}."
            )
        if len(set(atoms)) != len(atoms):
            raise ValueError("Scan coordinate atom indices must be distinct.")
        return self


class CalculationScanCoordinateCreate(CalculationScanCoordinatePayload, SchemaBase):
    pass


class CalculationScanCoordinateUpdate(SchemaBase):
    coordinate_index: int | None = Field(default=None, ge=1)
    coordinate_kind: str | None = Field(default=None, pattern=r"^(bond|angle|dihedral|improper)$")
    atom1_index: int | None = Field(default=None, ge=1)
    atom2_index: int | None = Field(default=None, ge=1)
    atom3_index: int | None = Field(default=None, ge=1)
    atom4_index: int | None = Field(default=None, ge=1)
    step_count: int | None = Field(default=None, ge=1)
    step_size: float | None = None
    start_value: float | None = None
    end_value: float | None = None
    value_unit: CoordinateUnit | None = None
    resolution_degrees: float | None = None
    symmetry_number: int | None = Field(default=None, ge=1)


class CalculationScanCoordinateRead(CalculationScanCoordinatePayload, ORMBaseSchema):
    calculation_id: int


class CalculationConstraintPayload(BaseModel):
    constraint_index: int = Field(ge=1)
    constraint_kind: ConstraintKind
    atom1_index: int = Field(ge=1)
    atom2_index: int | None = Field(default=None, ge=1)
    atom3_index: int | None = Field(default=None, ge=1)
    atom4_index: int | None = Field(default=None, ge=1)
    target_value: float | None = None


class CalculationConstraintCreate(CalculationConstraintPayload, SchemaBase):
    pass


class CalculationConstraintUpdate(SchemaBase):
    constraint_index: int | None = Field(default=None, ge=1)
    constraint_kind: ConstraintKind | None = None
    atom1_index: int | None = Field(default=None, ge=1)
    atom2_index: int | None = Field(default=None, ge=1)
    atom3_index: int | None = Field(default=None, ge=1)
    atom4_index: int | None = Field(default=None, ge=1)
    target_value: float | None = None


class CalculationConstraintRead(CalculationConstraintPayload, ORMBaseSchema):
    calculation_id: int


class CalculationScanPointCoordinateValuePayload(BaseModel):
    coordinate_index: int = Field(ge=1)
    coordinate_value: float
    value_unit: CoordinateUnit | None = None


class CalculationScanPointCoordinateValueCreate(
    CalculationScanPointCoordinateValuePayload,
    SchemaBase,
):
    pass


class CalculationScanPointCoordinateValueUpdate(SchemaBase):
    coordinate_index: int | None = Field(default=None, ge=1)
    coordinate_value: float | None = None
    value_unit: CoordinateUnit | None = None


class CalculationScanPointCoordinateValueRead(
    CalculationScanPointCoordinateValuePayload,
    ORMBaseSchema,
):
    calculation_id: int
    point_index: int


class CalculationScanPointPayload(BaseModel):
    point_index: int = Field(ge=1)
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    geometry_id: int | None = None
    note: str | None = None


class CalculationScanPointCreate(CalculationScanPointPayload, SchemaBase):
    coordinate_values: list[CalculationScanPointCoordinateValueCreate] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_unique_coordinate_values(self) -> Self:
        coordinate_indices = [
            coordinate_value.coordinate_index
            for coordinate_value in self.coordinate_values
        ]
        if len(set(coordinate_indices)) != len(coordinate_indices):
            raise ValueError(
                "Scan point coordinate_index values must be unique within a point."
            )
        return self


class CalculationScanPointUpdate(SchemaBase):
    point_index: int | None = Field(default=None, ge=1)
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    geometry_id: int | None = None
    note: str | None = None


class CalculationScanPointRead(CalculationScanPointPayload, ORMBaseSchema):
    calculation_id: int
    coordinate_values: list[CalculationScanPointCoordinateValueRead] = Field(
        default_factory=list
    )


class CalculationScanResultPayload(BaseModel):
    dimension: int = Field(ge=1)
    is_relaxed: bool | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None


class CalculationScanResultCreate(CalculationScanResultPayload, SchemaBase):
    coordinates: list[CalculationScanCoordinateCreate] = Field(default_factory=list)
    constraints: list[CalculationConstraintCreate] = Field(default_factory=list)
    points: list[CalculationScanPointCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scan_bundle(self) -> Self:
        coordinate_indices = [
            coordinate.coordinate_index for coordinate in self.coordinates
        ]
        expected_coordinate_indices = list(range(1, self.dimension + 1))
        if sorted(coordinate_indices) != expected_coordinate_indices:
            raise ValueError(
                "Scan coordinate_index values must run contiguously from 1..dimension."
            )

        constraint_indices = [
            constraint.constraint_index for constraint in self.constraints
        ]
        if len(set(constraint_indices)) != len(constraint_indices):
            raise ValueError(
                "Scan constraint_index values must be unique within a scan result."
            )

        point_indices = [point.point_index for point in self.points]
        if len(set(point_indices)) != len(point_indices):
            raise ValueError(
                "Scan point_index values must be unique within a scan result."
            )

        valid_coordinate_indices = set(coordinate_indices)
        for point in self.points:
            for coordinate_value in point.coordinate_values:
                if coordinate_value.coordinate_index not in valid_coordinate_indices:
                    raise ValueError(
                        "Scan point coordinate values must reference defined scan coordinates."
                    )

        return self


class CalculationScanResultUpdate(SchemaBase):
    dimension: int | None = Field(default=None, ge=1)
    is_relaxed: bool | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None


class CalculationScanResultRead(CalculationScanResultPayload, ORMBaseSchema):
    calculation_id: int
    coordinates: list[CalculationScanCoordinateRead] = Field(default_factory=list)
    constraints: list[CalculationConstraintRead] = Field(default_factory=list)
    points: list[CalculationScanPointRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# IRC result and points (Phase 2)
# ---------------------------------------------------------------------------


class CalculationIRCPointRead(ORMBaseSchema):
    calculation_id: int
    point_index: int
    direction: IRCDirection | None = None
    is_ts: bool
    reaction_coordinate: float | None = None
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    max_gradient: float | None = None
    rms_gradient: float | None = None
    geometry_id: int | None = None
    note: str | None = None


class CalculationIRCResultRead(ORMBaseSchema):
    calculation_id: int
    direction: IRCDirection
    has_forward: bool
    has_reverse: bool
    ts_point_index: int | None = None
    point_count: int | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None
    points: list[CalculationIRCPointRead] = Field(default_factory=list)




# ---------------------------------------------------------------------------
# NEB image results (Phase 2)
# ---------------------------------------------------------------------------


class CalculationNEBImageResultRead(ORMBaseSchema):
    calculation_id: int
    image_index: int
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    path_distance_angstrom: float | None = None
    max_force: float | None = None
    rms_force: float | None = None
    is_climbing_image: bool




# ---------------------------------------------------------------------------
# Calculation parameters (Phase 2)
# ---------------------------------------------------------------------------


class CalculationParameterRead(TimestampedReadSchema):
    calculation_id: int
    raw_key: str
    canonical_key: str | None = None
    raw_value: str
    canonical_value: str | None = None
    section: str | None = None
    value_type: str | None = None
    unit: str | None = None
    parameter_index: int | None = None


# ---------------------------------------------------------------------------
# Geometry validation (Phase 2)
# ---------------------------------------------------------------------------


class CalculationGeometryValidationRead(ORMBaseSchema):
    """Inherits ORMBaseSchema (not TimestampedReadSchema) because the PK is
    calculation_id, not a surrogate id column. TimestampedReadSchema assumes
    id: int which does not exist on this table."""

    calculation_id: int
    created_at: datetime
    input_geometry_id: int | None = None
    output_geometry_id: int | None = None
    species_smiles: str
    is_isomorphic: bool
    rmsd: float | None = None
    atom_mapping: dict | None = None
    n_mappings: int | None = None
    validation_status: ValidationStatus
    validation_reason: str | None = None
    rmsd_warning_threshold: float | None = None
