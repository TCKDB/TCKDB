"""Read schemas for /api/v1/scientific/transport/search.

Records reuse :class:`ScientificTransportRecord` from the detail
endpoint so search and detail callers parse responses with one set
of code.

See ``backend/docs/specs/scientific_transport_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
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
    CollapseMode,
    Pagination,
    ReviewStatusSummary,
    SelectionPolicy,
)
from app.schemas.reads.scientific_transport import (
    ScientificTransportRecord,
)


class TransportSearchRequest(BaseModel):
    """Service-layer request for /scientific/transport/search.

    Filters AND-combine. At least one meaningful filter is required
    (422 ``missing_filter`` otherwise). Bool filter fields default
    to ``None``; explicit ``False`` is meaningful — only ``None``
    skips the filter gate.

    The ORM ``transport`` row has no ``model_kind`` column; the
    closest model-class signal is ``scientific_origin``
    (computed / experimental / estimated), surfaced here as the
    ``model_kind`` filter name to match the spec's vocabulary. The
    public ``ScientificOriginKind`` enum drives the column lookup.
    """

    # --- identity filters ------------------------------------------------
    species_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    species_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    transport_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- scalar filters --------------------------------------------------
    model_kind: ScientificOriginKind | None = None

    # --- evidence filters ------------------------------------------------
    has_source_calculations: bool | None = None
    has_lj_parameters: bool | None = None
    has_dipole_moment: bool | None = None
    has_polarizability: bool | None = None
    has_rotational_relaxation: bool | None = None

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


class ScientificTransportSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/transport/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificTransportRecord]
    pagination: Pagination


__all__ = [
    "RequestEcho",
    "ScientificTransportSearchResponse",
    "TransportSearchRequest",
]
