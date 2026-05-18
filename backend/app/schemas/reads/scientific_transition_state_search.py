"""Read schemas for /api/v1/scientific/transition-states/search.

Request + response envelope for the transition-state search endpoint.
Search returns records at the transition-state-entry grain because
entries are the concrete objects carrying charge/multiplicity/status
and the actual calculation evidence; the parent TS-concept context
travels along on each record.

See ``backend/docs/specs/scientific_transition_state_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
    RecordReviewStatus,
    TransitionStateEntryStatus,
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
from app.schemas.reads.scientific_transition_state import (
    ScientificTransitionStateEntryRecord,
)


class TransitionStatesSearchRequest(BaseModel):
    """Service-layer request for /scientific/transition-states/search.

    Filters AND-combine. At least one meaningful filter is required —
    requests with only pagination / include / review knobs are rejected
    with 422 ``missing_filter`` to avoid accidental public table scans.
    """

    # --- owner / parent filters ------------------------------------------
    reaction_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    reaction_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    transition_state_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    transition_state_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- TS-entry scalar filters -----------------------------------------
    status: TransitionStateEntryStatus | None = None
    charge: int | None = None
    multiplicity: int | None = Field(default=None, ge=1)

    # --- evidence filters ------------------------------------------------
    has_calculations: bool | None = None
    has_opt: bool | None = None
    has_freq: bool | None = None
    has_sp: bool | None = None
    has_irc: bool | None = None
    has_path_search: bool | None = None
    has_geometry_validation: bool | None = None
    has_scf_stability: bool | None = None

    # --- level-of-theory / software / workflow filters -------------------
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


class ScientificTransitionStatesSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/transition-states/search.

    Records are at the transition-state-entry grain.
    """

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificTransitionStateEntryRecord]
    pagination: Pagination


__all__ = [
    "RequestEcho",
    "ScientificTransitionStatesSearchResponse",
    "TransitionStatesSearchRequest",
]
