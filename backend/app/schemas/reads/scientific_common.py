"""Shared read fragments and request models for the /api/v1/scientific/* layer.

Defined once here, imported by the per-endpoint scientific read modules so the
fragment shapes (review summary, provenance summary, evidence breakdown,
temperature coverage, pagination) stay consistent across endpoints.

See docs/specs/read_api_mvp.md for the canonical contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.db.models.common import ProfileRecommendation, ReadProfile, RecordReviewStatus

# ---------------------------------------------------------------------------
# Request-side enums and shared knobs
# ---------------------------------------------------------------------------


class ProfiledRequestEcho(BaseModel):
    """Base class for every ``/scientific/*`` response's ``request`` echo.

    Every scientific response envelope carries a ``request`` block, and every
    such block subclasses this, so the resolved read profile is part of the
    published OpenAPI contract on **all** endpoints rather than something a
    consumer has to hope was applied.

    The values are stamped at the response boundary from the profile resolved
    once per request (``app/api/routes/scientific/_response.py``), not copied
    field-by-field at ~63 service construction sites — the defaults below are
    the correct answer for any caller who did not opt in, and they are also
    what unit tests that build a response object directly will see.

    ``profile``                 which contract the response answers under.
    ``profile_recommendation``  whether TCKDB endorses these records. Never
                                inferred from ``profile`` alone: a curated
                                read of a database with no published release
                                still reports ``none``.
    ``profile_release_ref``     the dataset release backing a curated read,
                                when one exists.

    All three are machine tokens. The prose explanation lives in
    ``backend/docs/specs/dataset_release_and_profiles.md``, never in the value.
    """

    profile: ReadProfile = ReadProfile.exploratory
    profile_recommendation: ProfileRecommendation = ProfileRecommendation.none
    profile_release_ref: str | None = None


class CollapseMode(str, Enum):
    """Collapse axis values per spec D4 / Phase 2.1.

    ``all``    return every eligible record after filter/sort/pagination.
    ``first``  return at most one record (zero or one) after filter and sort.
    """

    all = "all"
    first = "first"


class SelectionPolicy(str, Enum):
    """Named, read-time selection policy used when ``collapse=first``.

    A selection policy makes "show me one product for this species form" an
    *explicit, named* choice rather than an implicit one. Policies rank
    candidate products at read time only — none persists a curator decision.

    Policies that would require a stored choice (``benchmark_reference``,
    ``curator_pick``) are intentionally absent: they need the deferred
    product-selection persistence layer, not a read knob, and cannot be
    honestly evaluated from the record data alone. See
    ``backend/docs/specs/scientific_product_candidacy.md``.

    ``default``        the endpoint's standard ranking.
    ``latest``         most recently created first.
    ``most_reviewed``  best review status first, then most recent.
    """

    default = "default"
    latest = "latest"
    most_reviewed = "most_reviewed"


# Map review status to ranking key per L2. Lower wins.
#
# ``under_review`` ranks above ``not_reviewed`` and stays there. Before
# revision ``c1d8f4a25b30`` that looked wrong, because every deposited record
# was stamped ``under_review`` and the rank was sorting the whole corpus above
# an empty set. The label was the defect, not the order: ``under_review`` now
# means a curator has the record open, and a record someone is looking at is a
# better bet than one nobody has touched. Do not reorder these two.
REVIEW_RANK: dict[RecordReviewStatus, int] = {
    RecordReviewStatus.approved: 0,
    RecordReviewStatus.under_review: 1,
    RecordReviewStatus.not_reviewed: 2,
    RecordReviewStatus.deprecated: 3,
    RecordReviewStatus.rejected: 4,
}


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Pagination(BaseModel):
    """Echoed pagination block per L5.

    ``total``                pre-collapse, post-filter match count.
    ``post_collapse_total``  count after collapse, before page slicing.
    ``returned``             actual length of ``records``.
    """

    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_LIMIT)
    returned: int = Field(ge=0)
    total: int = Field(ge=0)
    post_collapse_total: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Review fragments
# ---------------------------------------------------------------------------


class ReviewStatusSummary(BaseModel):
    """Counts per review status across a candidate record set (pre-collapse)."""

    approved: int = 0
    under_review: int = 0
    not_reviewed: int = 0
    deprecated: int = 0
    rejected: int = 0
    total: int = 0


class RecordReviewBadge(BaseModel):
    """Single record's direct review state — no chain traversal (D7)."""

    status: RecordReviewStatus
    reviewed_at: datetime | None = None
    reviewer_kind: Literal["human", "automated", "system"] | None = None


# ---------------------------------------------------------------------------
# Supersession notice
# ---------------------------------------------------------------------------


class SupersessionNotice(BaseModel):
    """Correction notice attached to a record that has been replaced.

    Why this exists
    ---------------
    TCKDB never rewrites an accepted scientific record. A correction is a
    *new* record plus an immutable
    ``scientific_record_supersession`` edge saying "this was replaced by
    that, here is why, by whom, when". Keeping the old row byte-identical is
    what makes an existing citation keep resolving.

    Resolving is not enough. A citation that 404s announces its own problem;
    a citation that resolves cleanly to a *superseded* number looks healthy,
    so nobody investigates. Findable-and-unmarked is precisely the failure
    the append-only design was meant to prevent, so every read of a
    superseded record carries this block — unconditionally, not behind an
    ``include=`` token. See ADR 0003 (frozen rows) and ADR 0007 (no stored
    ``is_current``).

    Two pointers, deliberately
    --------------------------
    ``superseded_by``  the **immediate** successor. Truthful about the one
                       edge that was recorded, and preserves the history: a
                       reader walking A → B → C sees the real steps.
    ``current``        the **head** of the chain — what a reader actually
                       wants to follow. For a one-link chain the two are
                       equal; for A → B → C a read of A reports
                       ``superseded_by=B``, ``current=C``.

    Neither pointer is stored. The database holds only the chain of edges;
    the head is computed per read. Storing the head would mean ``UPDATE``-ing
    every earlier record whenever a new correction lands — forbidden by the
    accepted-science immutability triggers, and the same second-source-of-
    truth defect ADR 0007 rejected as ``is_current``. Appending a correction
    is one ``INSERT``, and every read of every earlier record reports the new
    head immediately because it was never written down.

    ``reason`` and ``superseded_at`` describe the **immediate** edge, matching
    ``superseded_by``. Whoever recorded the edge is deliberately not named
    here: the read API does not attribute curation actions to a person.

    Both pointers are public refs of the *records* (``thm_…``, ``kin_…``),
    never of the supersession edge, and never a database row id
    (DR-0028 Req 2).
    """

    superseded_by: str
    current: str
    reason: str
    superseded_at: datetime
    #: Number of recorded edges between this record and ``current``. ``1``
    #: means ``superseded_by`` *is* the head; ``>1`` means this record has
    #: been corrected more than once since. A client that only wants "is
    #: there anything newer than my immediate successor" can read this
    #: instead of walking the chain itself.
    chain_length: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Level of theory / software / workflow tool / literature summaries
# ---------------------------------------------------------------------------


class LevelOfTheorySummary(BaseModel):
    """Lightweight LoT shape used in scientific provenance summaries.

    Phase B: ``level_of_theory_ref`` is the public stable handle; the
    existing ``level_of_theory_id`` integer stays for the compatibility
    window. See ``docs/specs/public_identifier_policy.md``.
    """

    level_of_theory_id: int
    level_of_theory_ref: str
    method: str
    basis: str | None = None
    dispersion: str | None = None
    solvent: str | None = None
    label: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display(self) -> str:
        """``method/basis`` — the way a chemist writes a level of theory.

        Derived, never stored and never accepted on input: it is exactly
        ``method`` and ``basis`` rendered the conventional way, and
        ``method`` alone when there is no basis (a semi-empirical or
        composite method has none, and ``"AM1/"`` would be a worse answer
        than ``"AM1"``).

        It exists because the fields it is built from are already here and
        a human reading the JSON should not have to assemble them. It is
        deliberately *not* an identity: two different
        ``level_of_theory`` rows can render the same string when they
        differ only in dispersion, solvent or spin treatment.
        ``level_of_theory_ref`` is the handle to compare on; ``display``
        is for reading. Nothing should key, group or deduplicate on it.
        """
        if self.basis:
            return f"{self.method}/{self.basis}"
        return self.method


class SoftwareReleaseSummary(BaseModel):
    """Software release pointer used in provenance summaries."""

    software_release_id: int
    software_release_ref: str
    software: str
    version: str | None = None


class WorkflowToolReleaseSummary(BaseModel):
    """Workflow tool release pointer used in provenance summaries."""

    workflow_tool_release_id: int
    workflow_tool_release_ref: str
    workflow_tool: str
    version: str | None = None


class LiteratureSummary(BaseModel):
    """Minimal literature reference used in provenance summaries.

    A canonical literature read model lives in
    ``app/schemas/entities/literature.py`` (LiteratureRead). This summary is a
    deliberately smaller shape sufficient for scientific-read provenance,
    avoiding a heavier include for what is usually a sidebar fact.

    Phase B: ``literature_ref`` is the public stable handle; ``id`` stays
    for the compatibility window.
    """

    id: int
    literature_ref: str
    title: str | None = None
    year: int | None = None
    doi: str | None = None


# ---------------------------------------------------------------------------
# Calculation / validation fragments
# ---------------------------------------------------------------------------


# Geometry validation values per Phase 2.3 spec patch.
GeometryValidationStatus = Literal["passed", "warning", "fail", "not_present"]

# SCF stability values per Phase 2.3 spec patch.
SCFStabilityStatusValue = Literal[
    "stable", "unstable", "stabilized", "inconclusive", "not_present"
]


class ValidationSummary(BaseModel):
    """Geometry validation outcome for a single calculation.

    Phase B: ``calculation_ref`` is the public stable handle for the
    associated calculation; ``calculation_id`` stays for compatibility.
    """

    status: GeometryValidationStatus
    calculation_id: int
    calculation_ref: str | None = None


class SCFStabilitySummary(BaseModel):
    """SCF wavefunction stability outcome for a single calculation."""

    status: SCFStabilityStatusValue
    calculation_id: int
    calculation_ref: str | None = None


class CalculationEvidenceSummary(BaseModel):
    """Lightweight per-calculation summary embedded in provenance blocks."""

    calculation_id: int
    calculation_ref: str | None = None
    calculation_type: str
    converged: bool | None = None
    geometry_validation_status: GeometryValidationStatus
    scf_stability_status: SCFStabilityStatusValue
    level_of_theory: LevelOfTheorySummary | None = None
    software: SoftwareReleaseSummary | None = None


class PathSearchSummary(BaseModel):
    """Path-search calculation summary used in TS-backed kinetics provenance."""

    calculation_id: int
    calculation_ref: str | None = None
    method: str | None = None
    converged: bool | None = None


# ---------------------------------------------------------------------------
# Temperature coverage and evidence completeness
# ---------------------------------------------------------------------------


class TemperatureCoverage(BaseModel):
    """D8 verbatim — full-range coverage gate plus extrapolation distance.

    ``overlap_fraction`` is diagnostic only; per D8 it is never the primary
    sort score.
    """

    requested_min_k: float | None = None
    requested_max_k: float | None = None
    record_min_k: float | None = None
    record_max_k: float | None = None
    covers_requested_range: bool
    overlap_fraction: float | None = None
    extrapolation_distance_k: float


class EvidenceCompletenessBreakdown(BaseModel):
    """L1 — score plus auditable per-predicate checklist.

    The outer shape is stable across endpoints; the ``checklist`` keys are
    endpoint-specific. Use ``model_config(extra="allow")`` so each endpoint
    can attach its own checklist keys without redefining the model.
    """

    model_config = ConfigDict(extra="allow")

    score: int = Field(ge=0)
    max: int = Field(ge=0)
    checklist: dict[str, bool]


# ---------------------------------------------------------------------------
# Default-trust filter knobs
# ---------------------------------------------------------------------------


def default_visible_statuses(
    *, include_rejected: bool = False, include_deprecated: bool = False
) -> set[RecordReviewStatus]:
    """Set of statuses that should be visible by default per D5.

    Approved / under_review / not_reviewed are always visible. Rejected and
    deprecated are excluded unless explicitly opted in.
    """
    statuses = {
        RecordReviewStatus.approved,
        RecordReviewStatus.under_review,
        RecordReviewStatus.not_reviewed,
    }
    if include_rejected:
        statuses.add(RecordReviewStatus.rejected)
    if include_deprecated:
        statuses.add(RecordReviewStatus.deprecated)
    return statuses


def status_at_or_above(threshold: RecordReviewStatus) -> set[RecordReviewStatus]:
    """Set of statuses with ``review_rank <= threshold's rank`` (better or equal)."""
    threshold_rank = REVIEW_RANK[threshold]
    return {s for s, rank in REVIEW_RANK.items() if rank <= threshold_rank}


def simple_selection_sort_key(
    record_id: int,
    *,
    policy: SelectionPolicy,
    review_status_by_id: dict[int, RecordReviewStatus],
    created_at_by_id: dict[int, datetime],
) -> tuple:
    """Ranking key for review/recency-only candidate sets (statmech / transport).

    Used by the per-species statmech and transport reads, whose record shapes
    share only review status, created_at, and id (no temperature coverage or
    evidence score like thermo). ``latest`` ranks purely by recency;
    ``default`` and ``most_reviewed`` rank by review status first (the
    historical per-species order). All policies break ties by created_at DESC
    then id DESC so the order is total and deterministic.
    """
    ts = created_at_by_id[record_id].timestamp()
    if policy is SelectionPolicy.latest:
        return (-ts, -record_id)
    rank = REVIEW_RANK[review_status_by_id[record_id]]
    return (rank, -ts, -record_id)
