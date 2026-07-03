"""Read schemas for /api/v1/scientific/network-solves/search.

Records reuse :class:`ScientificNetworkSolveRecord` from the
network-solve detail endpoint so search and detail callers parse
responses with one set of code.

Naming note: the request schema uses ``solve_method`` to filter on
``NetworkSolve.me_method`` (the master-equation algorithm — free
text like ``"RRKM/ME"`` / ``"CSE"``). The ``method`` / ``basis``
fields target the **source-calculation** level-of-theory join, same
as the network search surface. This matches the convention used by
the network search (where ``method`` is the LoT method) and keeps
the public API ergonomically aligned.

See ``backend/docs/specs/scientific_network_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import RecordReviewStatus
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_WORKFLOW_TOOL_LENGTH as _MAX_WORKFLOW_TOOL_LENGTH,
)
from app.schemas.reads.scientific_common import (
    Pagination,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_network import (
    ScientificNetworkSolveRecord,
)


class NetworkSolveSearchRequest(BaseModel):
    """Service-layer request for /scientific/network-solves/search.

    Filters AND-combine. At least one meaningful filter is required.
    Bool filter fields default to ``None``; explicit ``False`` is
    meaningful.
    """

    # --- identity filters ------------------------------------------------
    network_solve_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    network_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- scalar filters --------------------------------------------------
    # ``solve_method`` matches NetworkSolve.me_method (free-text ME algorithm).
    solve_method: str | None = Field(default=None, max_length=128)

    # --- T/P envelope filters (overlap semantics, see spec §11.x) --------
    temperature_min: float | None = None
    temperature_max: float | None = None
    pressure_min: float | None = None
    pressure_max: float | None = None

    # --- evidence filters ------------------------------------------------
    has_bath_gas: bool | None = None
    has_energy_transfer: bool | None = None
    has_source_calculations: bool | None = None
    has_kinetics: bool | None = None
    has_chebyshev: bool | None = None
    has_plog: bool | None = None
    has_point_kinetics: bool | None = None

    # --- provenance filters (through source calcs) -----------------------
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
    filter: dict[str, Any]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificNetworkSolveSearchResponse(BaseModel):
    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificNetworkSolveRecord]
    pagination: Pagination


__all__ = [
    "NetworkSolveSearchRequest",
    "RequestEcho",
    "ScientificNetworkSolveSearchResponse",
]
