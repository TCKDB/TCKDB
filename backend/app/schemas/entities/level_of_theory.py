from pydantic import BaseModel, Field

from app.db.models.common import CoreTreatment, SpinTreatment
from app.schemas.common import SchemaBase, TimestampedReadSchema


class LevelOfTheoryBase(BaseModel):
    """Common scientific provenance fields for a level of theory."""

    method: str = Field(min_length=1)
    basis: str | None = None
    aux_basis: str | None = None
    cabs_basis: str | None = None
    dispersion: str | None = None
    solvent: str | None = None
    solvent_model: str | None = None
    keywords: str | None = None


class LevelOfTheoryCreate(LevelOfTheoryBase, SchemaBase):
    """Internal create schema with backend-derived lot hash."""

    lot_hash: str = Field(min_length=64, max_length=64)


class LevelOfTheoryUpdate(SchemaBase):
    """Update schema for editable LoT fields. Only for administrative purposes"""

    method: str | None = Field(default=None, min_length=1)
    basis: str | None = None
    aux_basis: str | None = None
    cabs_basis: str | None = None
    dispersion: str | None = None
    solvent: str | None = None
    solvent_model: str | None = None
    keywords: str | None = None


class LevelOfTheoryRead(LevelOfTheoryBase, TimestampedReadSchema):
    """Read schema returned by the API."""

    lot_hash: str
    #: Restricted / unrestricted / restricted-open. ``None`` means the producer did not state it,
    #: which is not the same as the enum's own ``unknown`` member. Part of the level's identity.
    spin_treatment: SpinTreatment | None = None
    #: Frozen-core or all-electron (ADR 0021). ``None`` means the producer did not state it, never
    #: "frozen core by default". Part of the level's identity only when set.
    core_treatment: CoreTreatment | None = None
