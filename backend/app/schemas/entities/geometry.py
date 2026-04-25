from typing import List

from pydantic import BaseModel, Field

from app.schemas.common import ORMBaseSchema, SchemaBase, TimestampedReadSchema


class GeometryAtomBase(BaseModel):
    """Base schema with common geometry fields."""

    atom_index: int = Field(ge=1)
    element: str = Field(min_length=1, max_length=2)
    x: float
    y: float
    z: float


class GeometryAtomRead(GeometryAtomBase, ORMBaseSchema):
    """Read schema with common geometry fields."""

    geometry_id: int


class GeometryBase(BaseModel):
    """Base schema with common geometry fields."""

    natoms: int = Field(ge=1)
    xyz_text: str | None = None


class GeometryCreate(GeometryBase, SchemaBase):
    """
    Create schema with common geometry fields.
    Allows the backend to insert atoms together with the geometry.
    """

    geom_hash: str = Field(min_length=64, max_length=64)
    atoms: List[GeometryAtomBase]


class GeometryRead(GeometryBase, TimestampedReadSchema):
    """Read schema with common geometry fields."""

    geom_hash: str = Field(min_length=64, max_length=64)
    atoms: List[GeometryAtomRead]
