"""Read schemas for /api/v1/scientific/reaction-entries/{id}/full.

Composite document — joins species, kinetics, transition states, calculations,
review summary in one response. See docs/specs/read_api_mvp.md §Endpoint 5.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import (
    CalculationEvidenceSummary,
    PathSearchSummary,
    RecordReviewBadge,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_kinetics import KineticsRecord


class ReviewDetail(str, Enum):
    """``include_review`` query parameter values."""

    summary = "summary"
    full = "full"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ReactionFullReadRequest(BaseModel):
    """Service-layer request for /full."""

    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    sort: str | None = None  # rejected non-None per v0 sort policy.
    include: list[str] = Field(default_factory=list)
    include_review: ReviewDetail = ReviewDetail.summary


# ---------------------------------------------------------------------------
# Per-section shapes
# ---------------------------------------------------------------------------


class ReactionEntrySummary(BaseModel):
    """Top-level reaction-entry header for the /full response.

    Phase B: ``reaction_ref`` and ``reaction_entry_ref`` are the public
    stable handles alongside the integer IDs.
    """

    id: int
    reaction_entry_ref: str
    reaction_id: int
    reaction_ref: str
    equation: str
    reversible: bool
    family: str | None = None
    review: RecordReviewBadge


class ReactionFullSpeciesParticipant(BaseModel):
    """Reactant or product side participant in /full.

    Phase B: ``species_entry_ref`` is the public stable handle for the
    participant species entry.
    """

    species_entry_id: int
    species_entry_ref: str
    smiles: str
    participant_index: int
    review: RecordReviewBadge


class ReactionFullSpecies(BaseModel):
    """Species sub-section."""

    reactants: list[ReactionFullSpeciesParticipant]
    products: list[ReactionFullSpeciesParticipant]


class TransitionStateCalculationSlot(BaseModel):
    """One calculation slot within the TS sub-record (ts_opt, ts_freq, etc.).

    Phase B: ``calculation_ref`` is the public stable handle alongside
    ``calculation_id``.
    """

    calculation_id: int
    calculation_ref: str
    type: str
    method: str | None = None  # populated for path-search calcs


class TransitionStateDependency(BaseModel):
    """Edge in the TS calculation dependency graph.

    Phase B: ``parent_calculation_ref`` and ``child_calculation_ref`` are
    the public stable handles alongside the integer IDs.
    """

    parent_calculation_id: int
    parent_calculation_ref: str
    child_calculation_id: int
    child_calculation_ref: str
    role: str


class TransitionStateInFull(BaseModel):
    """Transition-state record embedded in /full.

    Phase B: ``transition_state_entry_ref`` is the public stable handle
    alongside ``transition_state_entry_id``.
    """

    transition_state_entry_id: int
    transition_state_entry_ref: str
    review: RecordReviewBadge
    calculations: dict[str, TransitionStateCalculationSlot] = Field(default_factory=dict)
    dependencies: list[TransitionStateDependency] = Field(default_factory=list)


class ReviewRecordEntry(BaseModel):
    """Audit-array entry returned only when ``include_review=full``."""

    record_type: str
    record_id: int
    status: RecordReviewStatus
    reviewed_at: datetime | None = None


class RequestEcho(BaseModel):
    """Echo of the parsed query."""

    include: list[str]
    include_review: ReviewDetail


class ScientificReactionFullResponse(BaseModel):
    """Response envelope for /api/v1/scientific/reaction-entries/{id}/full.

    Sections that are not in the ``include`` set are omitted entirely.
    Sections that are in the ``include`` set are always present (collections
    as ``[]``, objects as ``null`` when empty).
    """

    request: RequestEcho
    reaction_entry: ReactionEntrySummary
    review_summary: ReviewStatusSummary

    # Always present when ``include`` covers them; absent otherwise.
    species: ReactionFullSpecies | None = None
    kinetics: list[KineticsRecord] | None = None
    transition_states: list[TransitionStateInFull] | None = None
    calculations: list[CalculationEvidenceSummary] | None = None
    path_search: list[PathSearchSummary] | None = None
    irc: list[dict[str, object]] | None = None
    scans: list[dict[str, object]] | None = None
    conformers: list[dict[str, object]] | None = None
    artifacts: list[dict[str, object]] | None = None

    # Present only when include_review=full.
    review_records: list[ReviewRecordEntry] | None = None
