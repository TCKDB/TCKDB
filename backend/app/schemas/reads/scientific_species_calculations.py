"""Read schemas for /api/v1/scientific/species-calculations/search.

Chemistry-first species calculation/conformer search. Calculation-centered
records that include species identity, energy (when available), level of
theory, software, conformer context (nullable), geometry IDs, validation,
and review state — so workflow tools can decide reuse without hidden policy.

See docs/specs/species_calculation_search_api.md for the contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.db.models.common import (
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConformerSelectionKind,
    RecordReviewStatus,
    ScientificOriginKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.reads.scientific_common import (
    CollapseMode,
    LevelOfTheorySummary,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
    SCFStabilitySummary,
    SoftwareReleaseSummary,
    ValidationSummary,
    WorkflowToolReleaseSummary,
)


# ---------------------------------------------------------------------------
# Ranking enum (Phase 7 spec — measurable values only)
# ---------------------------------------------------------------------------


class CalculationRanking(str, Enum):
    """Allowed v0 ranking values for species-calculation search.

    See spec §Ranking semantics. ``lowest_energy`` is only legal when the
    request also supplies ``calculation_type=sp`` or ``calculation_type=opt``.
    """

    default = "default"
    latest = "latest"
    earliest = "earliest"
    review_rank = "review_rank"
    lowest_energy = "lowest_energy"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class SpeciesCalculationsSearchRequest(BaseModel):
    """Service-layer request for chemistry-first species calculation search.

    At least one species identifier (chemistry filter or explicit handle)
    is required. Multiple identifiers AND-combine; inconsistent identifiers
    return an empty result set. ``species_entry_id`` (when supplied) is a
    handle path — 404 if the entry does not exist.
    """

    # Chemistry-first species identity filters
    smiles: str | None = None
    inchi: str | None = None
    inchi_key: str | None = None
    formula: str | None = None
    charge: int | None = None
    multiplicity: int | None = None
    electronic_state_kind: SpeciesEntryStateKind | None = None
    species_entry_kind: StationaryPointKind | None = None

    # Explicit handles (optional)
    species_id: int | None = None
    species_entry_id: int | None = None

    # Calculation filters
    calculation_type: CalculationType | None = None
    level_of_theory_id: int | None = None
    method: str | None = None
    basis: str | None = None
    software: str | None = None
    workflow_tool: str | None = None
    scientific_origin: ScientificOriginKind | None = None
    calculation_quality: CalculationQuality | None = None

    # Trust filters (shallow per D7)
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False
    # Orthogonal to review's ``rejected`` — opts in CalculationQuality.rejected.
    include_rejected_quality: bool = False

    # Sort / collapse / pagination
    ranking: CalculationRanking = CalculationRanking.default
    sort: str | None = None  # rejected non-None per v0 sort policy
    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


# ---------------------------------------------------------------------------
# Per-record blocks
# ---------------------------------------------------------------------------


class SpeciesCalculationsSpeciesContext(BaseModel):
    """Resolved species/species-entry identity context for a calculation."""

    species_id: int
    species_entry_id: int
    canonical_smiles: str
    inchi_key: str
    charge: int
    multiplicity: int
    species_entry_kind: StationaryPointKind
    electronic_state_kind: SpeciesEntryStateKind


class CalculationCoreBlock(BaseModel):
    """Direct calculation-row metadata."""

    calculation_id: int
    calculation_type: CalculationType
    calculation_quality: CalculationQuality
    created_at: datetime
    review: RecordReviewBadge


class CalculationEnergyBlock(BaseModel):
    """Energy summary — present only for ``sp`` / ``opt`` calculations.

    For other calculation types this block is ``null`` in the response
    (the field is always present so the JSON shape stays stable).
    """

    energy_hartree: float | None = None
    energy_kind: str  # "electronic_energy" for sp; "final_energy" for opt


class ConformerContextBlock(BaseModel):
    """Conformer context — null when calculation has no conformer observation.

    The service must never fabricate conformer associations; this block is
    populated only when ``Calculation.conformer_observation_id`` is set.
    """

    conformer_observation_id: int
    conformer_group_id: int
    conformer_assignment_scheme_id: int | None = None
    conformer_group_label: str | None = None
    # In v0 a compact summary by default; full JSON via ``include=conformers``.
    torsion_fingerprint_json: dict[str, object] | None = None
    selection_kinds: list[ConformerSelectionKind] = Field(default_factory=list)


class GeometryBlock(BaseModel):
    """Geometry IDs and metadata. Full XYZ deferred per spec."""

    primary_output_geometry_id: int | None = None
    primary_output_geometry_role: CalculationGeometryRole | None = None
    input_geometry_ids: list[int] = Field(default_factory=list)
    output_geometry_ids: list[int] = Field(default_factory=list)


class ValidationBlock(BaseModel):
    """Geometry validation + SCF stability summaries (Phase 2.3 vocabularies)."""

    geometry_validation: ValidationSummary | None = None
    scf_stability: SCFStabilitySummary | None = None


class CalculationProvenanceBlock(BaseModel):
    """Calculation-level provenance summary."""

    supporting_calculation_ids: list[int] = Field(default_factory=list)
    submission_id: int | None = None
    artifacts_available: bool = False


class SpeciesCalculationsSearchRecord(BaseModel):
    """One result row: species + calculation + energy + conformer/geometry/validation."""

    species: SpeciesCalculationsSpeciesContext
    calculation: CalculationCoreBlock
    energy: CalculationEnergyBlock | None = None
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    conformer: ConformerContextBlock | None = None
    geometry: GeometryBlock
    validation: ValidationBlock
    provenance: CalculationProvenanceBlock


class RequestEcho(BaseModel):
    """Echo of the parsed query."""

    filter: dict[str, object]
    ranking: CalculationRanking
    sort: str
    collapse: CollapseMode
    include: list[str]


class ScientificSpeciesCalculationsSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/species-calculations/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[SpeciesCalculationsSearchRecord]
    pagination: Pagination
