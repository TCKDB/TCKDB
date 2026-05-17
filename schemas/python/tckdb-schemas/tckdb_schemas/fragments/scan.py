"""Scan-result upload fragments — the scan tree extracted from the
backend ``app.schemas.entities.calculation`` module.

Carries scan coordinates, scan point coordinate values, scan points,
and the scan result wrapper. The shape matches the calculation upload
payload's ``scan_result`` field so producers can build full scan
calculations through the wire contract.

Backend read/update shapes for the same DB rows remain in the backend
package.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import CoordinateUnit
from tckdb_schemas.fragments.calculation import CalculationConstraintCreate
from tckdb_schemas.fragments.geometry import GeometryPayload


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


class CalculationScanPointCoordinateValuePayload(BaseModel):
    coordinate_index: int = Field(ge=1)
    coordinate_value: float
    value_unit: CoordinateUnit | None = None


class CalculationScanPointCoordinateValueCreate(
    CalculationScanPointCoordinateValuePayload,
    SchemaBase,
):
    pass


class CalculationScanPointPayload(BaseModel):
    """One sampled point on a scan surface.

    Bundle producers should set ``geometry`` (inline ``GeometryPayload``);
    the workflow resolves and dedupes it via the geometry hash and
    populates ``calc_scan_point.geometry_id``. ``geometry_id`` is kept for
    primitive/internal callers that already hold a resolved geometry row.
    The two are mutually exclusive.
    """

    point_index: int = Field(ge=1)
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    geometry_id: int | None = None
    geometry: GeometryPayload | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_geometry_exclusive(self) -> Self:
        if self.geometry is not None and self.geometry_id is not None:
            raise ValueError(
                "Scan point may set either 'geometry' (inline) or 'geometry_id' "
                "(resolved row), not both."
            )
        return self


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
