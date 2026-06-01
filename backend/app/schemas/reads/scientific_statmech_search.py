"""Read schemas for /api/v1/scientific/statmech/search.

Records reuse :class:`ScientificStatmechRecord` from the detail
endpoint so search and detail callers parse responses with one set
of code.

See ``backend/docs/specs/scientific_statmech_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
    RecordReviewStatus,
    StatmechTreatmentKind,
)
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
    MAX_WORKFLOW_TOOL_LENGTH as _MAX_WORKFLOW_TOOL_LENGTH,
)
from app.schemas.reads.scientific_common import (
    CollapseMode,
    Pagination,
    ReviewStatusSummary,
    SelectionPolicy,
)
from app.schemas.reads.scientific_statmech import (
    ScientificStatmechRecord,
)


class StatmechSearchRequest(BaseModel):
    """Service-layer request for /scientific/statmech/search.

    Filters AND-combine. At least one meaningful filter is required —
    requests with only pagination / include / review knobs are
    rejected with 422 ``missing_filter``.
    """

    # --- identity filters ------------------------------------------------
    species_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    species_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    statmech_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    conformer_group_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    conformer_observation_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- statmech scalar filters -----------------------------------------
    model_kind: StatmechTreatmentKind | None = None

    # --- evidence filters ------------------------------------------------
    has_source_calculations: bool | None = None
    has_freq_calculation: bool | None = None
    has_rotor_scans: bool | None = None
    has_torsions: bool | None = None

    # --- provenance filters ----------------------------------------------
    method: str | None = Field(default=None, max_length=_MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=_MAX_BASIS_LENGTH)
    software: str | None = Field(
        default=None, max_length=_MAX_SOFTWARE_NAME_LENGTH
    )
    software_version: str | None = Field(default=None, max_length=128)
    workflow_tool: str | None = Field(
        default=None, max_length=_MAX_WORKFLOW_TOOL_LENGTH
    )
    workflow_tool_version: str | None = Field(default=None, max_length=128)

    # --- review filters --------------------------------------------------
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # --- sort / include / pagination -------------------------------------
    sort: str | None = None  # rejected non-None per v0 sort policy
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class RequestEcho(BaseModel):
    """Echo of the parsed request — surfaced in the response envelope.

    ``collapse`` / ``selection_policy`` are populated by the per-species
    subresource read (which supports an explicit named single-record policy)
    and left ``null`` by broad search, which always returns all candidates.
    """

    filter: dict[str, Any]
    sort: str
    include: list[str] = Field(default_factory=list)
    collapse: CollapseMode | None = None
    selection_policy: SelectionPolicy | None = None


class ScientificStatmechSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/statmech/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificStatmechRecord]
    pagination: Pagination


__all__ = [
    "RequestEcho",
    "ScientificStatmechSearchResponse",
    "StatmechSearchRequest",
]
