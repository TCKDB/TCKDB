"""Read schema for ``GET /scientific/species/name-search``: species found by a name."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind
from app.schemas.reads.scientific_common import HiddenByDefault, OrderEcho, ProfiledRequestEcho, ScientificResponse

NAME_ORDER_STATEMENT = (
    "Alphabetical by normalised name, then by species reference. This is not a ranking: the first match is not a "
    "better match than the last."
)

TRUNCATED_STATEMENT = "More species match than are listed. Refine your search."


class NameMatchMode(str, Enum):
    exact = "exact"
    prefix = "prefix"


class SpeciesNameSearchRequestEcho(ProfiledRequestEcho):
    #: The normalised query the search compared against.
    query_key: str
    match: NameMatchMode
    limit: int
    hidden_by_default: HiddenByDefault | None = Field(
        None,
        description="Always null: hidden species are not counted. merged_refs is empty too, as the request names no ref.",
    )


class SpeciesNameMatch(BaseModel):
    """One species found by one of its names."""

    species_ref: str
    formula: str | None
    smiles: str
    name: str
    kind: SpeciesNameKind
    source_kind: SpeciesNameSourceKind


class SpeciesNameSearchResponse(ScientificResponse):
    request: SpeciesNameSearchRequestEcho
    matches: list[SpeciesNameMatch]
    #: True when more species match than ``limit``; refine the query.
    truncated: bool
    order: OrderEcho
