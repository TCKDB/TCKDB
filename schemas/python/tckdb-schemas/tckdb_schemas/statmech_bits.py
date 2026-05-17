"""Shared statmech upload fragments.

Carries only ``StatmechTorsionCoordinateIn``, the slim atom-quartet
definition reused by both the standalone statmech upload and the
computed-species / computed-reaction bundle endpoints.

The full ``StatmechUploadRequest`` (and its torsion/source-calc/etc.
container classes) stay backend-side because they orchestrate
service-layer resolution and ownership checks.
"""

from typing import Self

from pydantic import Field, model_validator

from tckdb_schemas.common import SchemaBase


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
