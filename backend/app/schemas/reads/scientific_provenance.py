"""Read schemas for /api/v1/scientific/reaction-entries/{id}/full.

Composite document — joins species, kinetics, transition states, calculations,
review summary in one response. See docs/specs/read_api_mvp.md §Endpoint 5.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models.common import RecordReviewStatus, TransitionStateEntryStatus
from app.schemas.reads.scientific_calculation import (
    CalculationIRCSummary,
    CalculationPathSearchSummary,
    CalculationScanSummary,
)
from app.schemas.reads.scientific_common import (
    CalculationEvidenceSummary,
    PathSearchSummary,
    RecordReviewBadge,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_kinetics import KineticsRecord
from app.schemas.reads.scientific_transition_state import (
    TransitionStateCalculationEvidenceSummary,
)


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

    Carries the public refs that let a caller navigate to the new
    scientific TS read surface:

    - ``transition_state_ref`` → ``GET /scientific/transition-states/{ref}``
    - ``transition_state_entry_ref`` →
      ``GET /scientific/transition-state-entries/{ref}``
    - ``calculations[*].calculation_ref`` →
      ``GET /scientific/calculations/{ref}``

    Integer ``*_id`` siblings are Phase D policy-gated. ``status``
    and ``evidence_summary`` mirror the corresponding fields on
    :class:`ScientificTransitionStateEntryRecord` so the
    full-response block lines up byte-for-byte with the per-entry
    detail endpoint (same counts, same booleans, same status enum).
    """

    transition_state_id: int | None = None
    transition_state_ref: str
    transition_state_entry_id: int
    transition_state_entry_ref: str
    status: TransitionStateEntryStatus | None = None
    review: RecordReviewBadge
    evidence_summary: TransitionStateCalculationEvidenceSummary
    calculations: dict[str, TransitionStateCalculationSlot] = Field(default_factory=dict)
    dependencies: list[TransitionStateDependency] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Path-section items (scan / IRC / path-search) — summary-only
# ---------------------------------------------------------------------------
#
# Each item is a per-calculation projection that mirrors the
# corresponding ``include=scan|irc|path_search`` heavy-include block on
# the calculation detail endpoint. The `summary` field is **byte-identical**
# to ``record.scan|irc|path_search`` from the calc detail endpoint;
# the path-data point arrays remain available **only** behind the
# specialized endpoints below (the ``endpoint`` field is a public
# navigation hint, not a payload):
#
# - ``GET /api/v1/scientific/calculations/{ref}/scan``
# - ``GET /api/v1/scientific/calculations/{ref}/irc``
# - ``GET /api/v1/scientific/calculations/{ref}/path-search``


class ReactionFullScanItem(BaseModel):
    """One scan calculation embedded under ``/full?include=scans``.

    ``summary`` is the same shape that
    ``GET /scientific/calculations/{ref}?include=scan`` returns under
    ``record.scan`` — coordinate list + result-row fields + bounded
    aggregates. No per-point arrays, no coordinate-value rows, no XYZ.
    ``endpoint`` carries the ref-based navigation hint for the
    specialized full-data endpoint.
    """

    calculation_id: int | None = None
    calculation_ref: str
    endpoint: str
    summary: CalculationScanSummary | None = None


class ReactionFullIRCItem(BaseModel):
    """One IRC calculation embedded under ``/full?include=irc``.

    ``summary`` is byte-identical to
    ``GET /scientific/calculations/{ref}?include=irc`` under
    ``record.irc``. Per-point arrays live behind the specialized
    ``/irc`` endpoint.
    """

    calculation_id: int | None = None
    calculation_ref: str
    endpoint: str
    summary: CalculationIRCSummary | None = None


class ReactionFullPathSearchItem(BaseModel):
    """One path-search calculation embedded under ``/full?include=path_search``.

    ``summary`` is byte-identical to
    ``GET /scientific/calculations/{ref}?include=path_search`` under
    ``record.path_search``. Per-point arrays live behind the
    specialized ``/path-search`` endpoint.
    """

    calculation_id: int | None = None
    calculation_ref: str
    endpoint: str
    summary: CalculationPathSearchSummary | None = None


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
    path_search: list[ReactionFullPathSearchItem] | None = None
    irc: list[ReactionFullIRCItem] | None = None
    scans: list[ReactionFullScanItem] | None = None
    conformers: list[dict[str, object]] | None = None
    artifacts: list[dict[str, object]] | None = None

    # Present only when include_review=full.
    review_records: list[ReviewRecordEntry] | None = None
