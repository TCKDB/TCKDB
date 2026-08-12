"""Shared statmech upload fragments.

Carries ``StatmechTorsionCoordinateIn``, the slim atom-quartet definition
reused by both the standalone statmech upload and the computed-species /
computed-reaction bundle endpoints, plus the ``*Create`` payloads nested
inside ``ConformerUploadRequest.statmech``.

The ``*Base`` classes here are plain ``BaseModel`` on purpose: the
backend's ``*Read`` and ``*Update`` schemas still inherit from them while
adding ``from_attributes=True`` ORM bases, so the field definitions have
exactly one home even though only the create side is on the wire.

The full ``StatmechUploadRequest`` (and its torsion/source-calc/etc.
container classes) stay backend-side because they orchestrate
service-layer resolution and ownership checks.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import StatmechCalculationRole, TorsionTreatmentKind


class StatmechTorsionCoordinateIn(SchemaBase):
    """Atom indices for one torsional coordinate in a standalone upload.

    :param coordinate_index: One-based coordinate number within the rotor.
    :param atom1_index: First atom index.
    :param atom2_index: Second atom index.
    :param atom3_index: Third atom index.
    :param atom4_index: Fourth atom index.
    """

    coordinate_index: int = Field(ge=1)
    atom1_index: int = Field(ge=1)
    atom2_index: int = Field(ge=1)
    atom3_index: int = Field(ge=1)
    atom4_index: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_distinct_atoms(self) -> Self:
        atoms = {
            self.atom1_index,
            self.atom2_index,
            self.atom3_index,
            self.atom4_index,
        }
        if len(atoms) != 4:
            raise ValueError("Torsion coordinate atom indices must be distinct.")
        return self


class StatmechSourceCalculationBase(BaseModel):
    """Shared fields for statmech source-calculation links.

    :param calculation_id: Referenced calculation row.
    :param role: Semantic role of the source calculation.
    """

    calculation_id: int
    role: StatmechCalculationRole


class StatmechSourceCalculationCreate(StatmechSourceCalculationBase, SchemaBase):
    """Nested create payload for a statmech source-calculation link."""


class StatmechTorsionCoordinateBase(BaseModel):
    """Shared fields for one torsional coordinate definition.

    :param coordinate_index: One-based coordinate number within the coupled rotor.
    :param atom1_index: First atom index in the torsion definition.
    :param atom2_index: Second atom index in the torsion definition.
    :param atom3_index: Third atom index in the torsion definition.
    :param atom4_index: Fourth atom index in the torsion definition.
    """

    coordinate_index: int = Field(ge=1)
    atom1_index: int = Field(ge=1)
    atom2_index: int = Field(ge=1)
    atom3_index: int = Field(ge=1)
    atom4_index: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_distinct_atoms(self) -> Self:
        atom_indices = {
            self.atom1_index,
            self.atom2_index,
            self.atom3_index,
            self.atom4_index,
        }
        if len(atom_indices) != 4:
            raise ValueError("Torsion coordinate atom indices must be distinct.")
        return self


class StatmechTorsionCoordinateCreate(
    StatmechTorsionCoordinateBase,
    SchemaBase,
):
    """Nested create payload for one torsional coordinate."""


class StatmechTorsionBase(BaseModel):
    """Shared fields for one statmech torsion.

    :param torsion_index: One-based torsion number within the statmech record.
    :param symmetry_number: Optional torsional symmetry number.
    :param treatment_kind: Optional torsion treatment kind.
    :param dimension: Number of coupled torsional coordinates in this rotor.
    :param top_description: Optional description of the rotating top.
    :param invalidated_reason: Optional reason why the torsion was invalidated.
    :param note: Optional free-text note.
    :param source_scan_calculation_id: Optional principal scan calculation for this torsion.
    """

    torsion_index: int = Field(ge=1)
    symmetry_number: int | None = Field(default=None, ge=1)
    treatment_kind: TorsionTreatmentKind | None = None

    dimension: int = Field(default=1, ge=1)
    top_description: str | None = None
    invalidated_reason: str | None = None
    note: str | None = None

    source_scan_calculation_id: int | None = None


class StatmechTorsionCreate(StatmechTorsionBase, SchemaBase):
    """Nested create payload for one statmech torsion.

    :param coordinates: Ordered torsional coordinate definitions. The number of
        coordinates must equal ``dimension``, and ``coordinate_index`` values
        must run contiguously from ``1..dimension``.
    """

    coordinates: list[StatmechTorsionCoordinateCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        if len(self.coordinates) != self.dimension:
            raise ValueError("Number of torsion coordinates must equal dimension.")

        coordinate_indices = [
            coordinate.coordinate_index for coordinate in self.coordinates
        ]
        expected_indices = list(range(1, self.dimension + 1))
        if sorted(coordinate_indices) != expected_indices:
            raise ValueError(
                "Torsion coordinate_index values must run contiguously from 1..dimension."
            )
        return self
