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
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_FORMULA_LENGTH as _MAX_FORMULA_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_INCHI_KEY_LENGTH as _MAX_INCHI_KEY_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_INCHI_LENGTH as _MAX_INCHI_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_SMILES_LENGTH as _MAX_SMILES_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_WORKFLOW_TOOL_LENGTH as _MAX_WORKFLOW_TOOL_LENGTH,
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
    smiles: str | None = Field(default=None, max_length=_MAX_SMILES_LENGTH)
    inchi: str | None = Field(default=None, max_length=_MAX_INCHI_LENGTH)
    inchi_key: str | None = Field(default=None, max_length=_MAX_INCHI_KEY_LENGTH)
    formula: str | None = Field(default=None, max_length=_MAX_FORMULA_LENGTH)
    charge: int | None = None
    multiplicity: int | None = None
    electronic_state_kind: SpeciesEntryStateKind | None = None
    species_entry_kind: StationaryPointKind | None = None

    # Explicit handles (optional)
    species_id: int | None = None
    species_entry_id: int | None = None
    # Phase C: ref siblings for species / species_entry identity handles.
    species_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)
    species_entry_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)

    # Calculation filters
    calculation_type: CalculationType | None = None
    level_of_theory_id: int | None = None
    level_of_theory_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    method: str | None = Field(default=None, max_length=_MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=_MAX_BASIS_LENGTH)
    software: str | None = Field(default=None, max_length=_MAX_SOFTWARE_NAME_LENGTH)
    workflow_tool: str | None = Field(
        default=None, max_length=_MAX_WORKFLOW_TOOL_LENGTH
    )
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
    """Resolved species/species-entry identity context for a calculation.

    Phase B: ``species_ref`` and ``species_entry_ref`` are the public stable
    handles alongside the integer IDs.
    """

    species_id: int
    species_ref: str
    species_entry_id: int
    species_entry_ref: str
    canonical_smiles: str
    inchi_key: str
    charge: int
    multiplicity: int
    species_entry_kind: StationaryPointKind
    electronic_state_kind: SpeciesEntryStateKind


class CalculationCoreBlock(BaseModel):
    """Direct calculation-row metadata.

    Phase B: ``calculation_ref`` is the public stable handle alongside
    ``calculation_id``.
    """

    calculation_id: int
    calculation_ref: str
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

    Phase B: ``*_ref`` siblings carry the public stable handles for the
    observation, group, and assignment scheme.
    """

    conformer_observation_id: int
    conformer_observation_ref: str
    conformer_group_id: int
    conformer_group_ref: str
    conformer_assignment_scheme_id: int | None = None
    conformer_assignment_scheme_ref: str | None = None
    conformer_group_label: str | None = None
    # In v0 a compact summary by default; full JSON via ``include=conformers``.
    torsion_fingerprint_json: dict[str, object] | None = None
    selection_kinds: list[ConformerSelectionKind] = Field(default_factory=list)


class GeometryRef(BaseModel):
    """Lightweight geometry reference pairing integer id with public ref."""

    geometry_id: int
    geometry_ref: str
    role: CalculationGeometryRole | None = None


class GeometryBlock(BaseModel):
    """Geometry IDs and metadata. Full XYZ deferred per spec.

    Phase B: ``primary_output_geometry_ref`` and the ``input_geometries`` /
    ``output_geometries`` object arrays carry the public stable handles. The
    bare ``*_id`` / ``*_ids`` fields are preserved for the compatibility
    window.
    """

    primary_output_geometry_id: int | None = None
    primary_output_geometry_ref: str | None = None
    primary_output_geometry_role: CalculationGeometryRole | None = None
    input_geometry_ids: list[int] = Field(default_factory=list)
    output_geometry_ids: list[int] = Field(default_factory=list)
    input_geometries: list[GeometryRef] = Field(default_factory=list)
    output_geometries: list[GeometryRef] = Field(default_factory=list)


class ValidationBlock(BaseModel):
    """Geometry validation + SCF stability summaries (Phase 2.3 vocabularies)."""

    geometry_validation: ValidationSummary | None = None
    scf_stability: SCFStabilitySummary | None = None


class SupportingCalculationRef(BaseModel):
    """One supporting calculation reference (id + public ref)."""

    calculation_id: int
    calculation_ref: str


class CalculationProvenanceBlock(BaseModel):
    """Calculation-level provenance summary.

    Phase B: ``supporting_calculations`` object array and ``submission_ref``
    carry the public stable handles alongside the legacy
    ``supporting_calculation_ids`` and ``submission_id`` fields.
    """

    supporting_calculation_ids: list[int] = Field(default_factory=list)
    supporting_calculations: list[SupportingCalculationRef] = Field(default_factory=list)
    submission_id: int | None = None
    submission_ref: str | None = None
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
