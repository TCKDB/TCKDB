"""Read schemas for /api/v1/scientific/network-kinetics/search.

Records reuse :class:`ScientificNetworkKineticsRecord` from the
network-kinetics detail endpoint so search and detail callers parse
responses with one set of code.

Naming notes:

- ``model_kind`` filters on ``NetworkKinetics.model_kind`` (the
  Chebyshev / PLOG / tabulated discriminator).
- ``method`` / ``basis`` / ``software`` / ``workflow_tool`` filters
  route through the parent solve's source-calc graph, matching the
  ``/scientific/network-solves/search`` convention.

``network_channel_ref`` is **not** exposed yet — ``NetworkChannel``
has no public ref. The same is true of channel-level identity
filters more broadly; the detail surface already addresses this by
returning channel context via composition_hash.

See ``backend/docs/specs/scientific_network_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
    NetworkKineticsModelKind,
    RecordReviewStatus,
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
from app.schemas.reads.scientific_network_kinetics import (
    ScientificNetworkKineticsRecord,
)


class NetworkKineticsSearchRequest(BaseModel):
    """Service-layer request for /scientific/network-kinetics/search.

    Filters AND-combine. At least one meaningful filter is required.
    Bool filter fields default to ``None``; explicit ``False`` is
    meaningful.
    """

    # --- identity filters ------------------------------------------------
    network_kinetics_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    network_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    network_solve_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- scalar filters --------------------------------------------------
    model_kind: NetworkKineticsModelKind | None = None

    # --- T/P envelope filters (overlap semantics) ------------------------
    temperature_min: float | None = None
    temperature_max: float | None = None
    pressure_min: float | None = None
    pressure_max: float | None = None

    # --- evidence filters ------------------------------------------------
    has_chebyshev: bool | None = None
    has_plog: bool | None = None
    has_points: bool | None = None
    has_source_calculations: bool | None = None

    # --- provenance filters (through parent solve's source calcs) --------
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

    # --- review filters (inherited from parent solve) --------------------
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


class ScientificNetworkKineticsSearchResponse(BaseModel):
    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificNetworkKineticsRecord]
    pagination: Pagination


__all__ = [
    "NetworkKineticsSearchRequest",
    "RequestEcho",
    "ScientificNetworkKineticsSearchResponse",
]
