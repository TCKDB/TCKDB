"""Read schemas for /api/v1/scientific/reaction-entries/{id}/kinetics.

See docs/specs/read_api_mvp.md §Endpoint 3.

Provenance keys are always present (Phase 2.2). TS-chain fields are populated
for TS-backed computational kinetics and ``null`` for non-TS-backed records
(experimental, estimated, imported, fitted, network-derived, literature-derived).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models.common import (
    ArrheniusAUnits,
    KineticsModelKind,
    KineticsUncertaintyKind,
    RecordReviewStatus,
    ScientificOriginKind,
)
from app.schemas.reads.scientific_common import (
    CollapseMode,
    EvidenceCompletenessBreakdown,
    LevelOfTheorySummary,
    LiteratureSummary,
    Pagination,
    PathSearchSummary,
    RecordReviewBadge,
    ReviewStatusSummary,
    SCFStabilitySummary,
    SoftwareReleaseSummary,
    TemperatureCoverage,
    ValidationSummary,
    WorkflowToolReleaseSummary,
)
from app.services.trust.models import TrustFragment

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class KineticsReadRequest(BaseModel):
    """Service-layer request model for reaction-entry kinetics read.

    The path-parameter reaction_entry_id is supplied separately to the
    service function (so this model stays useful for both GET and POST shapes).
    """

    temperature_min: float | None = None
    temperature_max: float | None = None
    pressure: float | None = None
    model_kind: KineticsModelKind | None = None
    level_of_theory_id: int | None = None
    # Phase C: LoT may be supplied by ref instead of (or alongside) id.
    level_of_theory_ref: str | None = None
    software: str | None = None

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


class ArrheniusParameters(BaseModel):
    """Arrhenius / modified-Arrhenius parameter block.

    Both ``arrhenius`` and ``modified_arrhenius`` use the same column shape
    in the underlying schema; ``n`` may be null for plain ``arrhenius`` records.
    """

    A: float | None = None
    A_units: ArrheniusAUnits | None = None
    n: float | None = None
    Ea_kj_mol: float | None = None


class KineticsUncertainty(BaseModel):
    """Uncertainty block — always returned, may have all-null entries."""

    A_uncertainty: float | None = None
    A_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    Ea_uncertainty_kj_mol: float | None = None


class KineticsProvenance(BaseModel):
    """Provenance block per Phase 2.2 — every key always present, ``null`` if absent.

    TS-chain fields populated only for TS-backed computational kinetics. The
    service must never fabricate TS links for non-TS-backed records.

    Phase B: ``*_ref`` siblings carry the public stable handles for each
    integer ``*_id`` field. The nested summary objects (path_search,
    primary_level_of_theory, primary_software, etc.) carry their own
    ``*_ref`` fields per their schemas.
    """

    transition_state_entry_id: int | None = None
    transition_state_entry_ref: str | None = None
    ts_opt_calculation_id: int | None = None
    ts_opt_calculation_ref: str | None = None
    ts_freq_calculation_id: int | None = None
    ts_freq_calculation_ref: str | None = None
    ts_sp_calculation_id: int | None = None
    ts_sp_calculation_ref: str | None = None
    path_search: PathSearchSummary | None = None
    irc: dict[str, object] | None = None
    primary_level_of_theory: LevelOfTheorySummary | None = None
    primary_software: SoftwareReleaseSummary | None = None
    geometry_validation: ValidationSummary | None = None
    scf_stability: SCFStabilitySummary | None = None
    literature: LiteratureSummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None


class KineticsRecord(BaseModel):
    """One kinetics record returned by the kinetics endpoint.

    Phase B: ``kinetics_ref`` is the public stable handle alongside
    ``kinetics_id``.
    """

    kinetics_id: int
    kinetics_ref: str
    scientific_origin: ScientificOriginKind
    model_kind: KineticsModelKind
    review: RecordReviewBadge
    parameters: ArrheniusParameters
    tunneling_model: str | None = None
    uncertainty: KineticsUncertainty
    temperature_coverage: TemperatureCoverage | None = None
    evidence_completeness: EvidenceCompletenessBreakdown
    provenance: KineticsProvenance
    trust: TrustFragment | None = None


class RequestEcho(BaseModel):
    """Echo of the parsed query."""

    filter: dict[str, object]
    sort: str
    collapse: CollapseMode
    include: list[str]


class ScientificReactionKineticsResponse(BaseModel):
    """Response envelope for /api/v1/scientific/reaction-entries/{id}/kinetics.

    Phase B: ``reaction_entry_ref`` mirrors ``reaction_entry_id`` as the
    public stable handle for the response's path-parameter resource.
    """

    request: RequestEcho
    reaction_entry_id: int
    reaction_entry_ref: str
    review_summary: ReviewStatusSummary
    records: list[KineticsRecord]
    pagination: Pagination
