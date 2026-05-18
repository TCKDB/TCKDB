"""Read schemas for /api/v1/scientific/networks/search."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import RecordReviewStatus
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
from app.schemas.reads.scientific_network import (
    ScientificNetworkRecord,
)


class NetworkSearchRequest(BaseModel):
    """Service-layer request for /scientific/networks/search.

    Filters AND-combine. At least one meaningful filter is required.
    Bool fields default to ``None``; explicit ``False`` is meaningful.
    """

    # --- identity filters ------------------------------------------------
    network_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    species_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    species_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    reaction_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    reaction_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- evidence filters ------------------------------------------------
    has_species: bool | None = None
    has_reactions: bool | None = None
    has_states: bool | None = None
    has_channels: bool | None = None
    has_solves: bool | None = None
    has_kinetics: bool | None = None
    has_chebyshev: bool | None = None
    has_plog: bool | None = None
    has_point_kinetics: bool | None = None

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

    # --- temperature / pressure envelope filters -------------------------
    # Match networks whose solve-level T/P envelope at least *touches*
    # the requested range. Documented as "overlap" semantics; tight
    # superset checks are deferred.
    temperature_min: float | None = None
    temperature_max: float | None = None
    pressure_min: float | None = None
    pressure_max: float | None = None

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
    filter: dict[str, Any]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificNetworkSearchResponse(BaseModel):
    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificNetworkRecord]
    pagination: Pagination


__all__ = [
    "NetworkSearchRequest",
    "RequestEcho",
    "ScientificNetworkSearchResponse",
]
