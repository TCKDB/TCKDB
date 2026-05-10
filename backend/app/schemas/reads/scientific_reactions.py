"""Read schemas for /api/v1/scientific/reactions/search.

See docs/specs/read_api_mvp.md §Endpoint 2.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import (
    CollapseMode,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
)


class ReactionDirectionQuery(str, Enum):
    """v0 reaction-search direction enum.

    ``forward``  — query reactants/products match in stored orientation.
    ``reverse``  — query reactants/products match the swapped orientation.
    ``either``   — match in either orientation.

    ``exact`` is **not** in v0; the service rejects it with a deterministic
    422 error (per Phase 2.1 patch).
    """

    forward = "forward"
    reverse = "reverse"
    either = "either"


class ReactionSearchRequest(BaseModel):
    """Service-layer request model for reaction search."""

    reactants: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    direction: ReactionDirectionQuery = ReactionDirectionQuery.either
    family: str | None = None

    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    sort: str | None = None  # rejected non-None per v0 sort policy.

    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


# ---------------------------------------------------------------------------
# Per-record shapes
# ---------------------------------------------------------------------------


class ReactionParticipantSummary(BaseModel):
    """Reactant or product participant within a reaction-entry record."""

    species_entry_id: int
    smiles: str
    participant_index: int


class ReactionAvailability(BaseModel):
    """Boolean availability flags + counts per L1."""

    has_kinetics: bool
    has_transition_state: bool
    has_path_search: bool
    kinetics_count: int


class ReactionScientificRecord(BaseModel):
    """One reaction-entry row from /scientific/reactions/search."""

    reaction_id: int
    reaction_entry_id: int
    equation: str
    matched_direction: ReactionDirectionQuery
    reversible: bool
    family: str | None = None
    review: RecordReviewBadge
    reactants: list[ReactionParticipantSummary]
    products: list[ReactionParticipantSummary]
    availability: ReactionAvailability


class RequestEcho(BaseModel):
    """Echo of the parsed query."""

    filter: dict[str, object]
    sort: str
    collapse: CollapseMode
    include: list[str]


class ScientificReactionSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/reactions/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ReactionScientificRecord]
    pagination: Pagination
