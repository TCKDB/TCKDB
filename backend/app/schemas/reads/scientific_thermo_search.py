"""Read schemas for /api/v1/scientific/thermo/search.

Chemistry-first thermo lookup: callers supply species identifiers and get
back fully-shaped thermo records with the resolved species/species_entry
identity attached. Reuses the per-record ``ThermoRecord`` shape from
``scientific_thermo`` so workflow tools see the same thermo block as the
entry-id detail endpoint.

See docs/specs/read_api_mvp.md and docs/guides/workflow_tool_scientific_reads.md.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.reads._field_bounds import (
    MAX_FORMULA_LENGTH as _MAX_FORMULA_LENGTH,
    MAX_INCHI_KEY_LENGTH as _MAX_INCHI_KEY_LENGTH,
    MAX_INCHI_LENGTH as _MAX_INCHI_LENGTH,
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
    MAX_SMILES_LENGTH as _MAX_SMILES_LENGTH,
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
)
from app.schemas.reads.scientific_common import (
    CollapseMode,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_thermo import ThermoModelKindQuery, ThermoRecord


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ThermoSearchRequest(BaseModel):
    """Service-layer request for chemistry-first thermo search.

    At least one species identifier (``smiles`` / ``inchi`` / ``inchi_key`` /
    ``formula``) must be supplied; multiple identifiers AND-combine.
    Inconsistent identifiers return an empty result set, not a 422.
    """

    # Species identity filters
    smiles: str | None = Field(default=None, max_length=_MAX_SMILES_LENGTH)
    inchi: str | None = Field(default=None, max_length=_MAX_INCHI_LENGTH)
    inchi_key: str | None = Field(default=None, max_length=_MAX_INCHI_KEY_LENGTH)
    formula: str | None = Field(default=None, max_length=_MAX_FORMULA_LENGTH)
    charge: int | None = None
    multiplicity: int | None = None
    electronic_state_kind: SpeciesEntryStateKind | None = None
    species_entry_kind: StationaryPointKind | None = None

    # Phase C: optional explicit handles for follow-up lookups.
    species_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)
    species_entry_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)

    # Thermo filters
    temperature_min: float | None = None
    temperature_max: float | None = None
    model_kind: ThermoModelKindQuery | None = None
    level_of_theory_id: int | None = None
    level_of_theory_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    software: str | None = Field(default=None, max_length=_MAX_SOFTWARE_NAME_LENGTH)

    # Trust filters
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # v0 forbids client-supplied sort.
    sort: str | None = None

    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


# ---------------------------------------------------------------------------
# Per-record + envelope
# ---------------------------------------------------------------------------


class ThermoSearchSpeciesContext(BaseModel):
    """Resolved species/species-entry identity context for a thermo record.

    Phase B: ``species_ref`` and ``species_entry_ref`` are the public
    stable handles alongside the integer IDs.
    """

    species_id: int
    species_ref: str
    canonical_smiles: str
    inchi_key: str
    charge: int
    multiplicity: int
    species_entry_id: int
    species_entry_ref: str
    species_entry_kind: StationaryPointKind
    electronic_state_kind: SpeciesEntryStateKind
    species_entry_review: RecordReviewBadge


class ThermoSearchRecord(BaseModel):
    """One result row: resolved species context + the thermo record itself.

    The ``thermo`` field is the same ``ThermoRecord`` shape returned by the
    entry-id detail endpoint, so workflow tools have a single thermo schema
    to depend on regardless of how they discovered the record.
    """

    species: ThermoSearchSpeciesContext
    thermo: ThermoRecord


class RequestEcho(BaseModel):
    """Echo of the parsed query."""

    filter: dict[str, object]
    sort: str
    collapse: CollapseMode
    include: list[str]


class ScientificThermoSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/thermo/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ThermoSearchRecord]
    pagination: Pagination
