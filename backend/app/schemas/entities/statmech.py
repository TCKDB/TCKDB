"""Statmech entity schemas.

The create-side payloads (``StatmechSourceCalculationCreate``,
``StatmechTorsionCreate``, ``StatmechTorsionCoordinateCreate``) and the
``*Base`` field definitions they share now live in
``tckdb_schemas.statmech_bits``: they are nested inside
``ConformerUploadRequest.statmech``, which is a published contract. The
``*Read`` / ``*Update`` schemas stay here — they carry ORM ids and
``from_attributes=True`` and have no place on the wire.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator
from tckdb_schemas.statmech_bits import (
    StatmechSourceCalculationBase,
    StatmechSourceCalculationCreate,
    StatmechTorsionBase,
    StatmechTorsionCoordinateBase,
    StatmechTorsionCoordinateCreate,
    StatmechTorsionCreate,
)

from app.db.models.common import (
    RigidRotorKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    TorsionTreatmentKind,
)
from app.schemas.common import ORMBaseSchema, SchemaBase, TimestampedCreatedByReadSchema

__all__ = [
    "StatmechBase",
    "StatmechCreate",
    "StatmechElectronicLevelBase",
    "StatmechElectronicLevelRead",
    "StatmechRead",
    "StatmechSourceCalculationBase",
    "StatmechSourceCalculationCreate",
    "StatmechSourceCalculationRead",
    "StatmechSourceCalculationUpdate",
    "StatmechTorsionBase",
    "StatmechTorsionCoordinateBase",
    "StatmechTorsionCoordinateCreate",
    "StatmechTorsionCoordinateRead",
    "StatmechTorsionCoordinateUpdate",
    "StatmechTorsionCreate",
    "StatmechTorsionRead",
    "StatmechTorsionUpdate",
    "StatmechUpdate",
]


class StatmechSourceCalculationRead(StatmechSourceCalculationBase, ORMBaseSchema):
    """Read schema for a statmech source-calculation link."""

    statmech_id: int


class StatmechSourceCalculationUpdate(SchemaBase):
    """Update schema for a statmech source-calculation link.

    This schema assumes the parent statmech id and calculation id come from the route.

    :param role: Optional replacement role.
    """

    role: StatmechCalculationRole | None = None


class StatmechTorsionCoordinateRead(StatmechTorsionCoordinateBase, ORMBaseSchema):
    """Read schema for one torsional coordinate."""

    torsion_id: int


class StatmechTorsionCoordinateUpdate(SchemaBase):
    """Update schema for one torsional coordinate.

    This schema assumes the parent torsion id and coordinate index come from the route.
    Distinct-atom validation runs only when all four atoms are supplied.

    :param coordinate_index: Optional replacement coordinate index.
    :param atom1_index: Optional first atom index.
    :param atom2_index: Optional second atom index.
    :param atom3_index: Optional third atom index.
    :param atom4_index: Optional fourth atom index.
    """

    coordinate_index: int | None = Field(default=None, ge=1)
    atom1_index: int | None = Field(default=None, ge=1)
    atom2_index: int | None = Field(default=None, ge=1)
    atom3_index: int | None = Field(default=None, ge=1)
    atom4_index: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_distinct_atoms_when_complete(self) -> Self:
        atom_indices = [
            self.atom1_index,
            self.atom2_index,
            self.atom3_index,
            self.atom4_index,
        ]
        if all(value is not None for value in atom_indices):
            if len(set(atom_indices)) != 4:
                raise ValueError("Torsion coordinate atom indices must be distinct.")
        return self


class StatmechTorsionRead(StatmechTorsionBase, TimestampedCreatedByReadSchema):
    """Read schema for one statmech torsion."""

    statmech_id: int
    coordinates: list[StatmechTorsionCoordinateRead] = Field(default_factory=list)


class StatmechTorsionUpdate(SchemaBase):
    """Update schema for one statmech torsion.

    This schema assumes the parent statmech id and torsion id come from the route.

    :param torsion_index: Optional replacement torsion index.
    :param symmetry_number: Optional replacement symmetry number.
    :param treatment_kind: Optional replacement treatment kind.
    :param dimension: Optional replacement dimension.
    :param top_description: Optional replacement top description.
    :param invalidated_reason: Optional replacement invalidation reason.
    :param note: Optional replacement note.
    :param source_scan_calculation_id: Optional replacement principal scan source.
    """

    torsion_index: int | None = Field(default=None, ge=1)
    symmetry_number: int | None = Field(default=None, ge=1)
    treatment_kind: TorsionTreatmentKind | None = None

    dimension: int | None = Field(default=None, ge=1)
    top_description: str | None = None
    invalidated_reason: str | None = None
    note: str | None = None

    source_scan_calculation_id: int | None = None


class StatmechElectronicLevelBase(BaseModel):
    """Shared fields for one electronic energy level.

    Low-lying electronic states drive the electronic partition function
    ``q_elec = Σ gᵢ·exp(−εᵢ/kT)`` (DR-0033). Each row is an ordered
    ``(energy, degeneracy)`` pair.

    :param level_index: One-based ordinal (ground state is ``1`` at
        ``energy_cm1=0``); excited states follow in ascending order.
    :param energy_cm1: Level energy above the ground state in cm⁻¹.
    :param degeneracy: Electronic degeneracy of the level.
    """

    level_index: int = Field(ge=1)
    energy_cm1: float = Field(ge=0)
    degeneracy: int = Field(ge=1)


class StatmechElectronicLevelRead(StatmechElectronicLevelBase, ORMBaseSchema):
    """Read schema for one statmech electronic level."""

    statmech_id: int


class StatmechBase(BaseModel):
    """Shared fields for the statmech resource.

    :param species_entry_id: Owning species-entry id.
    :param scientific_origin: Scientific origin category for this statmech record.
    :param literature_id: Optional linked literature row.
    :param workflow_tool_release_id: Optional workflow tool provenance.
    :param software_release_id: Optional software provenance.
    :param external_symmetry: Optional external symmetry number.
    :param point_group: Optional point-group label.
    :param is_linear: Optional linearity flag.
    :param rigid_rotor_kind: Optional rigid-rotor classification.
    :param statmech_treatment: Optional statmech treatment classification.
    :param freq_scale_factor: Optional frequency scale factor.
    :param uses_projected_frequencies: Optional projected-frequency flag.
    :param note: Optional free-text note.
    """

    species_entry_id: int
    scientific_origin: ScientificOriginKind

    literature_id: int | None = None
    workflow_tool_release_id: int | None = None
    software_release_id: int | None = None

    external_symmetry: int | None = Field(default=None, ge=1)
    point_group: str | None = None

    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    statmech_treatment: StatmechTreatmentKind | None = None

    freq_scale_factor: float | None = None
    uses_projected_frequencies: bool | None = None
    note: str | None = None


class StatmechCreate(StatmechBase, SchemaBase):
    """Create schema for a statmech resource.

    Nested creation is supported for source-calculation links and torsions.
    Parent foreign keys for those child rows are taken from the created statmech
    resource rather than from the payload.
    """

    source_calculations: list[StatmechSourceCalculationCreate] = Field(
        default_factory=list
    )
    torsions: list[StatmechTorsionCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_nested_uniqueness(self) -> Self:
        torsion_indices = [torsion.torsion_index for torsion in self.torsions]
        if len(set(torsion_indices)) != len(torsion_indices):
            raise ValueError("Torsion indices must be unique within a statmech record.")

        source_pairs = [
            (source.calculation_id, source.role) for source in self.source_calculations
        ]
        if len(set(source_pairs)) != len(source_pairs):
            raise ValueError(
                "Source calculation (calculation_id, role) pairs must be unique "
                "within a statmech record."
            )

        return self


class StatmechUpdate(SchemaBase):
    """Update schema for a statmech resource."""

    species_entry_id: int | None = None
    scientific_origin: ScientificOriginKind | None = None

    literature_id: int | None = None
    workflow_tool_release_id: int | None = None
    software_release_id: int | None = None

    external_symmetry: int | None = Field(default=None, ge=1)
    point_group: str | None = None

    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    statmech_treatment: StatmechTreatmentKind | None = None

    freq_scale_factor: float | None = None
    uses_projected_frequencies: bool | None = None
    note: str | None = None


class StatmechRead(StatmechBase, TimestampedCreatedByReadSchema):
    """Read schema for a statmech resource."""

    frequency_scale_factor_id: int | None = None

    source_calculations: list[StatmechSourceCalculationRead] = Field(
        default_factory=list
    )
    torsions: list[StatmechTorsionRead] = Field(default_factory=list)
    electronic_levels: list[StatmechElectronicLevelRead] = Field(
        default_factory=list
    )
