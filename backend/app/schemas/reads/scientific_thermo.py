"""Read schemas for /api/v1/scientific/species-entries/{id}/thermo.

See docs/specs/read_api_mvp.md §Endpoint 4.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.db.models.common import (
    GroupAdditivityComponentKind,
    RecordReviewStatus,
    ScientificOriginKind,
)
from app.schemas.reads.scientific_assessment import PublicAssessmentSummary
from app.schemas.reads.scientific_common import (
    CalculationEvidenceSummary,
    CollapseMode,
    EvidenceCompletenessBreakdown,
    LevelOfTheorySummary,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
    SelectionPolicy,
    SoftwareReleaseSummary,
    TemperatureCoverage,
)
from app.services.trust.models import TrustFragment


class ThermoModelKindQuery(str, Enum):
    """Thermo model kinds surfaced by the read API.

    ``nasa``     record is a NASA-7 fit (has a ThermoNASA row).
    ``nasa9``    record is a NASA-9 fit (has ThermoNASA9Interval rows).
    ``wilhoit``  record is a Wilhoit fit (has a ThermoWilhoit row).
    ``points``   record has ThermoPoint rows.
    ``scalar``   record has none of those — only h298/s298 scalar columns.

    ``nasa``/``points``/``scalar`` are retained unchanged for backward
    compatibility; ``nasa9`` and ``wilhoit`` were added alongside the
    NASA-9 / Wilhoit representations.
    """

    nasa = "nasa"
    nasa9 = "nasa9"
    wilhoit = "wilhoit"
    points = "points"
    scalar = "scalar"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ThermoReadRequest(BaseModel):
    """Service-layer request for species-entry thermo read."""

    temperature_min: float | None = None
    temperature_max: float | None = None
    model_kind: ThermoModelKindQuery | None = None
    level_of_theory_id: int | None = None
    # Phase C: LoT may be supplied by ref instead of (or alongside) id.
    level_of_theory_ref: str | None = None
    software: str | None = None

    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    sort: str | None = None  # rejected non-None per v0 sort policy.
    collapse: CollapseMode = CollapseMode.all
    # Named, read-time policy governing which single record collapse=first
    # selects. ``default`` keeps the standard thermo ranking.
    selection_policy: SelectionPolicy = SelectionPolicy.default
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


# ---------------------------------------------------------------------------
# Per-record shapes
# ---------------------------------------------------------------------------


class ThermoNASABlock(BaseModel):
    """Two-segment NASA polynomial block matching ThermoNASA columns."""

    t_low: float | None = None
    t_mid: float | None = None
    t_high: float | None = None
    low_temperature_coefficients: list[float | None] = Field(default_factory=list)
    high_temperature_coefficients: list[float | None] = Field(default_factory=list)


class ThermoNASA9IntervalBlock(BaseModel):
    """One NASA-9 polynomial interval matching ThermoNASA9Interval columns."""

    interval_index: int
    t_min_k: float
    t_max_k: float
    a1: float
    a2: float
    a3: float
    a4: float
    a5: float
    a6: float
    a7: float
    a8: float
    a9: float


class ThermoWilhoitBlock(BaseModel):
    """Wilhoit heat-capacity form matching ThermoWilhoit columns."""

    cp0_j_mol_k: float
    cp_inf_j_mol_k: float
    b_k: float
    a0: float
    a1: float
    a2: float
    a3: float
    h0_kj_mol: float | None = None
    s0_j_mol_k: float | None = None


class ThermoPointBlock(BaseModel):
    """One temperature-evaluated thermo point row."""

    temperature_k: float
    cp_j_mol_k: float | None = None
    h_kj_mol: float | None = None
    s_j_mol_k: float | None = None
    g_kj_mol: float | None = None


class ThermoProvenance(BaseModel):
    """Thermo provenance block — keys always present, ``null`` when absent.

    Phase B: integer ``*_id`` fields keep their place; ``*_ref`` siblings
    carry the public stable handles.
    """

    primary_calculation: CalculationEvidenceSummary | None = None
    level_of_theory: LevelOfTheorySummary | None = None
    software: SoftwareReleaseSummary | None = None
    statmech_id: int | None = None
    statmech_ref: str | None = None
    freq_calculation_id: int | None = None
    freq_calculation_ref: str | None = None
    sp_calculation_id: int | None = None
    sp_calculation_ref: str | None = None


class GroupAdditivityComponentBlock(BaseModel):
    """One Benson-group (or correction) contribution in a GA breakdown."""

    component_kind: GroupAdditivityComponentKind
    group_label: str
    count: int
    h298_contribution_kj_mol: float | None = None
    s298_contribution_j_mol_k: float | None = None
    cp298_contribution_j_mol_k: float | None = None


class GroupAdditivityBlock(BaseModel):
    """Group-additivity estimation provenance for an estimated thermo record.

    Present only when the thermo record has an ``applied_group_additivity``
    row (``scientific_origin=estimated``). Surfaces which GA scheme produced
    the estimate and the per-group contribution breakdown.
    """

    scheme_id: int
    scheme_ref: str
    scheme_name: str
    scheme_version: str | None = None
    code_commit: str | None = None
    note: str | None = None
    components: list[GroupAdditivityComponentBlock] = Field(default_factory=list)


class ThermoRecord(BaseModel):
    """One thermo record returned by the thermo endpoint.

    The underlying schema does not store cp units explicitly; per
    ``app/db/models/thermo.py``, ``ThermoPoint.cp_j_mol_k`` is the canonical
    (and only) cp representation in v0. Future schema work may surface a
    cp-units field; v0 omits it from the response.

    Phase B: ``thermo_ref`` is the public stable handle alongside the
    integer ``thermo_id``.
    """

    thermo_id: int
    thermo_ref: str
    scientific_origin: ScientificOriginKind
    model_kind: ThermoModelKindQuery
    review: RecordReviewBadge
    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None
    h298_uncertainty_kj_mol: float | None = None
    s298_uncertainty_j_mol_k: float | None = None
    nasa: ThermoNASABlock | None = None
    nasa9: list[ThermoNASA9IntervalBlock] | None = None
    wilhoit: ThermoWilhoitBlock | None = None
    points: list[ThermoPointBlock] | None = None
    temperature_coverage: TemperatureCoverage | None = None
    evidence_completeness: EvidenceCompletenessBreakdown
    provenance: ThermoProvenance
    # Group-additivity estimation breakdown; null unless the record is an
    # estimated thermo with an attached GA breakdown (DR-0035).
    group_additivity: GroupAdditivityBlock | None = None
    trust: TrustFragment | None = None
    assessments: PublicAssessmentSummary | None = None


class RequestEcho(BaseModel):
    """Echo of the parsed query."""

    filter: dict[str, object]
    sort: str
    collapse: CollapseMode
    selection_policy: SelectionPolicy = SelectionPolicy.default
    include: list[str]


class ScientificSpeciesThermoResponse(BaseModel):
    """Response envelope for /api/v1/scientific/species-entries/{id}/thermo.

    Phase B: ``species_entry_ref`` mirrors ``species_entry_id`` as the
    public stable handle for the response's path-parameter resource.
    """

    request: RequestEcho
    species_entry_id: int
    species_entry_ref: str
    review_summary: ReviewStatusSummary
    records: list[ThermoRecord]
    pagination: Pagination
