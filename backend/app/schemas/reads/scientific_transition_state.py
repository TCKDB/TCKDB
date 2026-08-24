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
from typing import Literal

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
    ProfiledRequestEcho,
    RecordReviewBadge,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.services.trust.models import TrustFragment

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class TransitionStateDetailRequest(BaseModel):
    """Service-layer request for the transition-state detail read."""

    include: list[str] = Field(default_factory=list)


class TransitionStateEntryDetailRequest(BaseModel):
    """Service-layer request for the transition-state-entry detail read."""

    include: list[str] = Field(default_factory=list)


class RequestEcho(ProfiledRequestEcho):
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


class TransitionStateEvidenceCoverage(BaseModel):
    """How many TS entries in scope carry each kind of evidence.

    Every value counts **entries**, never calculations. An entry with
    three ``freq`` calculations contributes ``1`` to ``freq``, not
    ``3``. The shared denominator is
    ``TransitionStateEvidenceSummary.entry_count``, so a caller reads
    each field as "*n* of *entry_count*"; a value can never exceed that
    denominator, and one that did would mean the query had started
    counting calculations again.

    What a full count does and does not say
    ---------------------------------------
    ``freq == entry_count`` says the coverage is **complete** — every
    candidate saddle point under this TS has at least one frequency
    calculation. It does **not** say those calculations are
    *comparable*: the entries may sit at different levels of theory,
    come from different codes, and describe different geometries. A
    count is honest about coverage, not about consistency, and no number
    in this block can stand in for that.

    Half of that gap is now answerable without a second request.
    ``TransitionStateEvidenceSummary.levels_of_theory``, beside this
    block, lists the levels used per calculation type — so
    ``freq == entry_count`` with two entries under ``freq`` is visibly a
    fully covered TS whose frequencies come from two levels. It still
    does not *assert* comparability; it just stops charging a round trip
    to find out. The software and geometry halves are still only under
    ``include=calculations``.

    ``0`` is exactly as strong as the old ``has_x is False`` was:
    nothing in scope carries that evidence.
    """

    opt: int
    freq: int
    sp: int
    irc: int
    path_search: int
    geometry_validation: int
    scf_stability: int


class TransitionStateEvidenceSummary(BaseModel):
    """Compact calculation-evidence projection for a TS **concept**.

    ``entry_count`` is the number of ``transition_state_entry`` rows
    under the TS — the same number as ``entries_summary.total``,
    repeated here so the coverage block is readable without
    cross-referencing another block. ``calculation_count`` is the number
    of calculations across all of those entries.
    ``evidence_coverage`` reports, per evidence kind, how many of the
    ``entry_count`` entries carry it.

    Primary calculation refs are deferred — the data model does not
    currently carry a unique notion of "primary" per type, so this block
    exposes counts only.

    Why counts here and booleans on the entry surface
    -------------------------------------------------
    This block deliberately has a **different shape** from
    :class:`TransitionStateEntryEvidenceSummary`. That asymmetry is the
    point, not an oversight, and it should not be smoothed over.

    A TS concept pools several candidate entries. The booleans this
    block used to carry (``has_opt`` / ``has_freq`` / …) were a plain OR
    across all of them, which made them asymmetrically informative:
    ``false`` was strong (nothing under the TS has it) while ``true``
    was nearly empty (one calculation under one entry made it ``true``).
    A reader seeing ``has_sp: true`` on a three-entry TS could not tell
    whether three entries had single points or one did. Counts answer
    that question — ``sp: 1`` against ``entry_count: 3`` shows an
    unevenly covered TS at a glance — and ``count > 0`` reproduces the
    retired boolean exactly, so nothing the boolean expressed was lost.

    A TS entry is a single candidate saddle point, so on that surface
    the booleans are unambiguous and are kept as they were.
    """

    entry_count: int
    calculation_count: int
    evidence_coverage: TransitionStateEvidenceCoverage
    #: Levels of theory used under this TS, per calculation type, pooled
    #: across every entry. See
    #: ``app/services/scientific_read/levels_of_theory.py`` for the shape's
    #: whole argument: lists because 12 of 34 deployed TS entries carry two
    #: levels, an absent key because that type has no calculation here, and
    #: an empty list because it has one that names no level. This is the
    #: block ``evidence_coverage``'s docstring says a count cannot stand in
    #: for — and it reports, it does not judge.
    levels_of_theory: dict[str, list[LevelOfTheorySummary]]


class TransitionStateEntryEvidenceSummary(BaseModel):
    """Compact calculation-evidence projection for one TS **entry**.

    Scope is this single entry: ``calculation_count`` and every ``has_*``
    boolean are restricted to the calculations whose
    ``transition_state_entry_id`` is this entry. A boolean is
    unambiguous here — it cannot pool a covered entry with an uncovered
    one, which is what made the same booleans misleading at TS-concept
    scope (see :class:`TransitionStateEvidenceSummary`).

    Counts and booleans are computed from cheap EXISTS-style queries
    against the calculation tables. Primary calculation refs are
    deferred for the same reason as on the concept block.

    As with the concept block's counts, ``has_freq: true`` says
    frequency evidence exists — not that it is comparable with the
    frequency evidence on any sibling entry.
    """

    calculation_count: int
    has_opt: bool
    has_freq: bool
    has_sp: bool
    has_irc: bool
    has_path_search: bool
    has_geometry_validation: bool
    has_scf_stability: bool
    #: Levels of theory used on this entry, per calculation type. The
    #: originating case for the whole block: an entry optimised at
    #: wb97xd/def2tzvp with its single point at MRCI+Davidson reports both,
    #: under the type each was run for, and no field anywhere claims the
    #: entry has *a* level. Keys mirror the ``has_*`` booleans above —
    #: ``has_sp`` false and an absent ``sp`` key are the same fact said
    #: twice. See ``app/services/scientific_read/levels_of_theory.py``.
    levels_of_theory: dict[str, list[LevelOfTheorySummary]]


class TransitionStateValidationEvidenceSummary(BaseModel):
    """Structured IRC validation evidence with a replayable source link.

    The two participant mappings say which saddle-point atoms become which
    declared participant, by index, and those indices count into
    ``transition_state_geometry_ref`` -- not into "the transition state",
    which has no atom order of its own. It is ``None`` exactly when both
    mappings are, since a record that partitions no atoms binds no indices.
    Named to match ``ReactionAtomMapDetail.transition_state_geometry_ref``,
    which is the same geometry playing the same role for the other surface
    that indexes the saddle point.
    """

    kind: str
    passed: bool
    rationale: str
    reconstruction_calculation_ref: str | None = None
    reactant_participant_mapping: dict[str, list[int]] | None = None
    product_participant_mapping: dict[str, list[int]] | None = None
    transition_state_geometry_ref: str | None = None


class TransitionStateEntryValidationEvidence(BaseModel):
    """One entry's validation evidence, on a TS-*concept* response.

    Keyed by entry ref rather than unioned across the concept's entries.
    A concept is a collection of entries computed at different levels of
    theory; an OR across them would report "validated" for a concept whose
    entries disagree, and would not say which entry carried the evidence.
    The list is per entry and empty for an entry that deposited none.
    """

    transition_state_entry_ref: str
    validation_evidence: list[TransitionStateValidationEvidenceSummary]


class TransitionStateValidationDescriptor(BaseModel):
    """One compact, machine-readable statement of validation status.

    Always present on a TS-entry record, independent of include tokens, so a
    consumer never has to infer "was this saddle point validated?" from the
    absence of an optional block. Values are machine tokens:

    - ``present``: a passed IRC evidence record exists.
    - ``failed``: an IRC evidence record exists but did not pass.
    - ``absent``: no IRC evidence was deposited.
    """

    irc: Literal["present", "absent", "failed"]


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
    has_validation_evidence: bool


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
    evidence_summary: TransitionStateEntryEvidenceSummary
    validation: TransitionStateValidationDescriptor
    available_sections: AvailableTransitionStateSections

    #: Every entry under this record's parent transition state, this one
    #: included — the same list the TS-concept surface returns under the
    #: same token. Populated under ``include=entries``, which is what an
    #: entry-grained record can answer only by looking up to its parent:
    #: *what else is under this transition state*. Never populated on a
    #: record that is itself nested inside another record's ``entries``.
    entries: list[ScientificTransitionStateEntryRecord] | None = None
    calculations: list[TransitionStateCalculationSummary] | None = None
    geometries: list[CalculationGeometryLinkSummary] | None = None
    review_history: list[TransitionStateReviewEntry] | None = None
    validation_evidence: list[TransitionStateValidationEvidenceSummary] | None = None

    # Deterministic trust / evidence fragment, populated under
    # ``include=trust`` on the standalone TS-entry detail surface and on
    # ``/transition-states/search``. ``include=all`` does not expand to the
    # token on either, and both routes strip the field when it was not
    # requested — so an absent ``trust`` means "you did not ask", never
    # "there is no verdict". A record nested inside another record's
    # ``entries`` block carries it on the same terms as its parent: the
    # token governs the whole response, not one depth of it.
    trust: TrustFragment | None = None


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
    evidence_summary: TransitionStateEvidenceSummary
    available_sections: AvailableTransitionStateSections
    entries: list[ScientificTransitionStateEntryRecord] | None = None
    calculations: list[TransitionStateCalculationSummary] | None = None
    geometries: list[CalculationGeometryLinkSummary] | None = None
    review_history: list[TransitionStateReviewEntry] | None = None

    #: IRC validation evidence for the concept's entries, one list per
    #: entry, populated under ``include=validation_evidence``. The token was
    #: legal on this surface before the field existed, so it was accepted,
    #: echoed, and produced nothing — while ``available_sections``
    #: advertised ``has_validation_evidence`` on the same record.
    validation_evidence: (
        list[TransitionStateEntryValidationEvidence] | None
    ) = None


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
    "TransitionStateCalculationSummary",
    "TransitionStateCoreBlock",
    "TransitionStateDetailRequest",
    "TransitionStateEntriesSummary",
    "TransitionStateEntryCoreBlock",
    "TransitionStateEntryDetailRequest",
    "TransitionStateEntryEvidenceSummary",
    "TransitionStateEntryValidationEvidence",
    "TransitionStateEvidenceCoverage",
    "TransitionStateEvidenceSummary",
    "TransitionStateReactionContext",
    "TransitionStateReviewEntry",
    "TransitionStateValidationEvidenceSummary",
]
