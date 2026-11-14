"""Request and read schemas for curated species names."""

from datetime import datetime

from pydantic import Field

from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind
from app.schemas.common import SchemaBase


class SpeciesNameCreate(SchemaBase):
    """Curator request to give a species a name. The species is named by its public ref."""

    species_ref: str = Field(min_length=1, max_length=64)
    name: str
    kind: SpeciesNameKind


class SpeciesNameRead(SchemaBase):
    """One name of one species."""

    name_ref: str
    species_ref: str
    name: str
    kind: SpeciesNameKind
    source_kind: SpeciesNameSourceKind
    created_at: datetime
    retired_at: datetime | None = None
    #: Username of the curator or admin who retired the name; null when live or not recorded.
    retired_by: str | None = None
