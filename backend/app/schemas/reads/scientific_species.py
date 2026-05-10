"""Read schemas for /api/v1/scientific/species/search.

See docs/specs/read_api_mvp.md §Endpoint 1.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.reads.scientific_common import (
    CollapseMode,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class SpeciesSearchRequest(BaseModel):
    """Service-layer request model for species search.

    At least one of ``smiles``, ``inchi``, ``inchi_key``, ``formula`` must be
    supplied; multiple identifiers AND-combine. Inconsistent identifiers
    return an empty result set, not a validation error (per Phase 2.1 patch).
    """

    smiles: str | None = None
    inchi: str | None = None
    inchi_key: str | None = None
    formula: str | None = None

    charge: int | None = None
    multiplicity: int | None = None
    electronic_state_kind: SpeciesEntryStateKind | None = None
    species_entry_kind: StationaryPointKind | None = None

    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # v0 forbids client-supplied sort. The service rejects a non-None value.
    sort: str | None = None

    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


# ---------------------------------------------------------------------------
# Per-record shapes
# ---------------------------------------------------------------------------


class SpeciesEntryAvailability(BaseModel):
    """Boolean availability flags + counts per L1 species/reaction-search policy."""

    has_thermo: bool
    has_statmech: bool
    has_transport: bool
    has_conformers: bool
    calculation_count: int


class SpeciesEntrySectionIds(BaseModel):
    """Lightweight section payload populated when an ``include=`` token requests it.

    v0 returns ID lists only; richer per-section read shapes are a future
    enhancement. Validation of the include token already happened upstream.
    """

    ids: list[int]


class SpeciesEntryScientificRecord(BaseModel):
    """Per-entry block embedded in a SpeciesScientificRecord."""

    species_entry_id: int
    species_entry_kind: StationaryPointKind
    electronic_state_kind: SpeciesEntryStateKind
    review: RecordReviewBadge
    availability: SpeciesEntryAvailability

    # Populated only when the corresponding include= token is set.
    thermo_summary: SpeciesEntrySectionIds | None = None
    statmech_summary: SpeciesEntrySectionIds | None = None
    transport_summary: SpeciesEntrySectionIds | None = None
    conformers_summary: SpeciesEntrySectionIds | None = None


class SpeciesScientificRecord(BaseModel):
    """One species row returned from /scientific/species/search."""

    species_id: int
    canonical_smiles: str
    inchi_key: str
    formula: str | None = None
    charge: int
    multiplicity: int
    entries: list[SpeciesEntryScientificRecord] = Field(default_factory=list)


class RequestEcho(BaseModel):
    """Echo of the parsed query for debuggability and traceability."""

    filter: dict[str, object]
    sort: str
    collapse: CollapseMode
    include: list[str]


class ScientificSpeciesSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/species/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[SpeciesScientificRecord]
    pagination: Pagination
