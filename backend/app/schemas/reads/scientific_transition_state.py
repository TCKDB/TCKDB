"""Read schemas for the scientific transition-state read surface.

Covers the detail endpoints:

- ``GET /api/v1/scientific/transition-states/{transition_state_ref_or_id}``
- ``GET /api/v1/scientific/transition-state-entries/{transition_state_entry_ref_or_id}``

The TS concept (``transition_state``) groups one or more candidate
``transition_state_entry`` rows under a single reaction-channel
interpretation. Both detail surfaces share the same per-record
``ScientificTransitionStateEntryRecord`` shape so callers can reuse one
parser across detail, the parent-TS view, and the search surface.

See ``backend/docs/specs/scientific_transition_state_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    TransitionStateEntryStatus,
)
from app.schemas.reads.scientific_calculation import (
    CalculationGeometryLinkSummary,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class TransitionStateDetailRequest(BaseModel):
    """Service-layer request for the transition-state detail read."""

    include: list[str] = Field(default_factory=list)


class TransitionStateEntryDetailRequest(BaseModel):
    """Service-layer request for the transition-state-entry detail read."""

    include: list[str] = Field(default_factory=list)


class RequestEcho(BaseModel):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core blocks
# ---------------------------------------------------------------------------


class TransitionStateCoreBlock(BaseModel):
    """Direct transition-state-row metadata.

    ``transition_state_ref`` is the public stable handle; the integer id
    is stripped when the deployment forbids exposing internal ids.
    """

    transition_state_id: int
    transition_state_ref: str
    label: str | None = None
    note: str | None = None
    created_at: datetime
    review: RecordReviewBadge


class TransitionStateEntryCoreBlock(BaseModel):
    """Direct transition-state-entry-row metadata.

    The RDKit ``mol`` blob is deliberately NOT exposed — only the
    ``unmapped_smiles`` text representation (which is a public, human-
    readable string) is surfaced. Charge/multiplicity/status are the
    minimum scientific identifiers a caller needs to interpret the row.
    """

    transition_state_entry_id: int
    transition_state_entry_ref: str
    charge: int
    multiplicity: int
    status: TransitionStateEntryStatus
    unmapped_smiles: str | None = None
    created_at: datetime
    review: RecordReviewBadge


# ---------------------------------------------------------------------------
# Reaction context
# ---------------------------------------------------------------------------


class TransitionStateReactionContext(BaseModel):
    """Lightweight reaction-entry context for a TS / TS-entry record.

    Carries refs (always present when the underlying row exists) and a
    rendered ``equation`` string (``"A + B <=> C + D"`` for reversible,
    ``"->"`` for irreversible). ``family`` is the reaction-family name
    when one is attached to the parent ``chem_reaction`` row.
    """

    reaction_id: int | None = None
    reaction_ref: str | None = None
    reaction_entry_id: int | None = None
    reaction_entry_ref: str | None = None
    equation: str | None = None
    reversible: bool | None = None
    family: str | None = None


# ---------------------------------------------------------------------------
# Calculation summary (compact)
# ---------------------------------------------------------------------------


class TransitionStateCalculationSummary(BaseModel):
    """Compact calculation projection embedded under a TS / TS-entry record.

    Carries enough provenance for a caller to decide whether to follow
    up with the full ``/scientific/calculations/{calculation_ref}``
    detail call. Heavy include sections (results, dependencies,
    parameters, constraints, scan/IRC/path-search points) are NOT
    surfaced here — they remain available on the calculation detail
    endpoint. ``primary_role`` records the dependency role this calc
    plays under the TS entry when known (``opt`` / ``freq`` / ``sp`` /
    ``irc`` / ``path_search`` calcs map directly to their type for
    convenience).
    """

    calculation_id: int
    calculation_ref: str
    type: CalculationType
    quality: CalculationQuality
    created_at: datetime
    review: RecordReviewBadge
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None


# ---------------------------------------------------------------------------
# Evidence summary
# ---------------------------------------------------------------------------


class TransitionStateCalculationEvidenceSummary(BaseModel):
    """Compact calculation-evidence projection for a TS or TS-entry.

    Counts and ``has_*`` booleans are computed from cheap EXISTS-style
    queries against the calculation tables under the TS entry. Primary
    calculation refs are deferred to a later PR — the data model does
    not currently carry a unique notion of "primary" per type, so we
    expose counts/booleans only.

    For a TS concept, the counts are summed across all entries under
    the TS. For a TS entry, the counts are restricted to that entry's
    calculations.
    """

    calculation_count: int
    has_opt: bool
    has_freq: bool
    has_sp: bool
    has_irc: bool
    has_path_search: bool
    has_geometry_validation: bool
    has_scf_stability: bool


# ---------------------------------------------------------------------------
# Available sections
# ---------------------------------------------------------------------------


class AvailableTransitionStateSections(BaseModel):
    """Boolean map describing which heavy include sections have data.

    Computed from cheap EXISTS queries so callers can avoid issuing
    follow-up requests for empty sections. All fields are always
    present; values reflect what an ``include=<token>`` would expand to.
    """

    has_entries: bool
    has_calculations: bool
    has_geometries: bool
    has_review: bool


# ---------------------------------------------------------------------------
# TS-entry record (also reused by the search response)
# ---------------------------------------------------------------------------


class TransitionStateReviewEntry(BaseModel):
    """One ``record_review`` row projected for the ``include=review`` token.

    The associated record is implicit (transition_state_entry or
    transition_state, depending on which detail surface returned the
    block). Internal user ids surface only when the deployment permits
    them — they are stripped by the Phase D visibility helper.
    """

    status: str
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None
    note: str | None = None


class ScientificTransitionStateEntryRecord(BaseModel):
    """One TS entry projected as a scientific record.

    Shared between the TS detail endpoint (one record per entry under
    the parent TS, populated when ``include=entries`` is supplied), the
    TS-entry detail endpoint (always one record), and the search
    surface (records list).
    """

    transition_state_entry: TransitionStateEntryCoreBlock
    transition_state: TransitionStateCoreBlock
    reaction: TransitionStateReactionContext
    evidence_summary: TransitionStateCalculationEvidenceSummary
    available_sections: AvailableTransitionStateSections
    calculations: list[TransitionStateCalculationSummary] | None = None
    geometries: list[CalculationGeometryLinkSummary] | None = None
    review_history: list[TransitionStateReviewEntry] | None = None


# ---------------------------------------------------------------------------
# TS-concept record
# ---------------------------------------------------------------------------


class TransitionStateEntriesSummary(BaseModel):
    """Counts of TS-entry rows under one TS concept, by status."""

    total: int
    by_status: dict[str, int] = Field(default_factory=dict)


class ScientificTransitionStateRecord(BaseModel):
    """One TS concept projected as a scientific record.

    The ``entries`` list is populated only under ``include=entries``;
    the same shape as :class:`ScientificTransitionStateEntryRecord` is
    reused so callers can parse both surfaces with one set of code.
    """

    transition_state: TransitionStateCoreBlock
    reaction: TransitionStateReactionContext
    entries_summary: TransitionStateEntriesSummary
    evidence_summary: TransitionStateCalculationEvidenceSummary
    available_sections: AvailableTransitionStateSections
    entries: list[ScientificTransitionStateEntryRecord] | None = None
    calculations: list[TransitionStateCalculationSummary] | None = None
    geometries: list[CalculationGeometryLinkSummary] | None = None
    review_history: list[TransitionStateReviewEntry] | None = None


# ---------------------------------------------------------------------------
# Response envelopes
# ---------------------------------------------------------------------------


class ScientificTransitionStateDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/transition-states/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificTransitionStateRecord


class ScientificTransitionStateEntryDetailResponse(BaseModel):
    """Response envelope for
    ``GET /scientific/transition-state-entries/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificTransitionStateEntryRecord


__all__ = [
    "AvailableTransitionStateSections",
    "RequestEcho",
    "ScientificTransitionStateDetailResponse",
    "ScientificTransitionStateEntryDetailResponse",
    "ScientificTransitionStateEntryRecord",
    "ScientificTransitionStateRecord",
    "TransitionStateCalculationEvidenceSummary",
    "TransitionStateCalculationSummary",
    "TransitionStateCoreBlock",
    "TransitionStateDetailRequest",
    "TransitionStateEntriesSummary",
    "TransitionStateEntryCoreBlock",
    "TransitionStateEntryDetailRequest",
    "TransitionStateReactionContext",
    "TransitionStateReviewEntry",
]
