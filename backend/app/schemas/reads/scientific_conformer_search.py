"""Read schemas for /api/v1/scientific/conformers/search.

Search returns records at the **conformer-group grain** — one record
per `conformer_group` row that matches the filter set. Per-record shape
is the same `ScientificConformerGroupRecord` used by the group detail
endpoint, so a generic client can parse search and detail responses
with one set of code.

See ``backend/docs/specs/scientific_conformer_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
    ConformerSelectionKind,
    RecordReviewStatus,
    ScientificOriginKind,
)
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
    MAX_WORKFLOW_TOOL_LENGTH as _MAX_WORKFLOW_TOOL_LENGTH,
)
from app.schemas.reads.scientific_common import (
    Pagination,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_conformer import (
    ScientificConformerGroupRecord,
)


class ConformersSearchRequest(BaseModel):
    """Service-layer request for /scientific/conformers/search.

    Filters AND-combine. At least one meaningful filter is required —
    requests with only pagination / include / review knobs are rejected
    with 422 ``missing_filter`` to avoid accidental public table scans.
    """

    # --- identity filters ------------------------------------------------
    species_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    species_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    conformer_group_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    conformer_observation_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- curation filters ------------------------------------------------
    selection_kind: ConformerSelectionKind | None = None
    has_selection: bool | None = None
    assignment_scheme_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- evidence filters ------------------------------------------------
    has_observations: bool | None = None
    has_calculations: bool | None = None
    has_geometries: bool | None = None
    has_opt: bool | None = None
    has_freq: bool | None = None
    has_sp: bool | None = None
    has_geometry_validation: bool | None = None
    has_scf_stability: bool | None = None

    # --- provenance filters ----------------------------------------------
    scientific_origin: ScientificOriginKind | None = None
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
    """Echo of the parsed request — surfaced in the response envelope."""

    filter: dict[str, Any]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificConformersSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/conformers/search.

    Records are at the conformer-group grain — one record per
    matching ``conformer_group`` row.
    """

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificConformerGroupRecord]
    pagination: Pagination


__all__ = [
    "ConformersSearchRequest",
    "RequestEcho",
    "ScientificConformersSearchResponse",
]
