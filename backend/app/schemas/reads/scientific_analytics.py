"""Request/response schemas for the bounded analytics surface.

Four endpoints — ``/api/v1/scientific/analytics/{kinetics,thermo,statmech,
calculations}`` — exist so that quantitative dataset construction has a small,
documented, indexed place to happen, instead of dozens of optional numeric
filters accreting onto every transactional search. See
``backend/docs/specs/scientific_analytics_surface.md``.

Three shape decisions, and why:

* **Flat records, not detail records.** The transactional searches return the
  same nested record the detail endpoint returns, which is right when a human
  is inspecting one record and wrong when a consumer is building a matrix of
  60,000 rows. Analytics records project the filterable columns and the
  identity refs and nothing else, so a page is a fixed number of scalars and
  the projection query needs no per-row round trip.
* **Every ``request`` echo subclasses**
  :class:`~app.schemas.reads.scientific_common.ProfiledRequestEcho`, which is
  what makes the resolved read profile part of the published contract here as
  it is everywhere else on ``/scientific/*``.
* **Pagination carries both contracts.** ``pagination`` is the shared
  offset/limit block, unchanged; ``next_cursor`` and ``watermark`` are the
  additive keyset contract. Offset callers can ignore them entirely.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    ArrheniusAUnits,
    CalculationQuality,
    CalculationType,
    KineticsDegeneracyConvention,
    KineticsDirection,
    KineticsModelKind,
    KineticsUncertaintyKind,
    PhaseKind,
    PressureContext,
    RecordReviewStatus,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechTreatmentKind,
    ThermoModelKind,
    TunnelingModel,
)
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH,
    MAX_METHOD_LENGTH,
    MAX_PUBLIC_REF_LENGTH,
    MAX_SOFTWARE_NAME_LENGTH,
    MAX_WORKFLOW_TOOL_LENGTH,
)
from app.schemas.reads.scientific_common import (
    Pagination,
    ProfiledRequestEcho,
    ReviewStatusSummary,
)

#: A cursor is base64 of a small JSON payload. The bound exists to reject a
#: 10 MB query parameter, not to validate the token — decoding does that.
MAX_CURSOR_LENGTH: int = 4096

#: Point groups are short symbols (``C2v``, ``D3h``, ``Ci``).
MAX_POINT_GROUP_LENGTH: int = 32


# ---------------------------------------------------------------------------
# Shared request knobs
# ---------------------------------------------------------------------------


class AnalyticsPagingRequest(BaseModel):
    """Knobs every analytics endpoint shares.

    ``cursor`` and ``offset`` are mutually exclusive: supplying both is a
    422 ``cursor_offset_conflict`` rather than a silent preference, because
    "I gave you an offset and you ignored it" is exactly the kind of quiet
    wrongness keyset paging exists to remove.
    """

    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    sort: str | None = None  # rejected when non-None, per the v0 sort policy
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50
    cursor: str | None = Field(default=None, max_length=MAX_CURSOR_LENGTH)


# ---------------------------------------------------------------------------
# Shared response fragments
# ---------------------------------------------------------------------------


class WatermarkEcho(BaseModel):
    """The snapshot bound in force for this traversal.

    ``taken_at`` is when the traversal's snapshot was established — the
    moment the first page was served, not the moment this page was. It is
    informational: the authoritative bound travels inside the opaque cursor.

    The bound itself is a record id and is therefore deliberately not echoed;
    internal integer ids are hidden from public responses by policy, and the
    caller never needs it.
    """

    taken_at: datetime
    release_ref: str | None = None


class AnalyticsRequestEcho(ProfiledRequestEcho):
    """Echo of the parsed request, plus the resolved read profile."""

    filter: dict[str, object]
    sort: str
    include: list[str] = Field(default_factory=list)
    #: ``offset`` or ``cursor`` — which traversal contract served this page.
    pagination_mode: str


# ---------------------------------------------------------------------------
# Kinetics
# ---------------------------------------------------------------------------


class KineticsAnalyticsRequest(AnalyticsPagingRequest):
    """Filters for ``/scientific/analytics/kinetics``. All AND-combine.

    Range filters are inclusive on both ends and independently optional; a
    ``*_min`` above its ``*_max`` is a 422 ``invalid_range`` rather than an
    empty page, because an empty page reads as "no such data".

    ``temperature_min_k`` / ``temperature_max_k`` are **coverage** filters,
    matching the semantics the transactional kinetics reads already use: a
    record matches when its own validity window contains the requested one
    (``tmin_k <= temperature_min_k`` and ``tmax_k >= temperature_max_k``). A
    record with no stated window does not match, because "unstated" is not
    "unbounded".
    """

    scientific_origin: ScientificOriginKind | None = None
    direction: KineticsDirection | None = None
    model_kind: KineticsModelKind | None = None
    tunneling_model: TunnelingModel | None = None
    pressure_context: PressureContext | None = None

    degeneracy_min: float | None = None
    degeneracy_max: float | None = None

    pressure_min_bar: float | None = None
    pressure_max_bar: float | None = None

    a_min: float | None = None
    a_max: float | None = None
    n_min: float | None = None
    n_max: float | None = None
    ea_min_kj_mol: float | None = None
    ea_max_kj_mol: float | None = None

    has_uncertainty: bool | None = None
    ea_uncertainty_min_kj_mol: float | None = None
    ea_uncertainty_max_kj_mol: float | None = None

    temperature_min_k: float | None = None
    temperature_max_k: float | None = None

    has_literature: bool | None = None
    workflow_tool: str | None = Field(
        default=None, max_length=MAX_WORKFLOW_TOOL_LENGTH
    )

    has_transition_state_provenance: bool | None = None
    has_statmech_provenance: bool | None = None


class KineticsAnalyticsRecord(BaseModel):
    """One kinetics row, flattened to its quantitative columns."""

    kinetics_id: int
    kinetics_ref: str
    reaction_entry_id: int
    reaction_entry_ref: str

    scientific_origin: ScientificOriginKind
    model_kind: KineticsModelKind
    direction: KineticsDirection | None = None
    is_third_body: bool

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    ea_kj_mol: float | None = None

    a_uncertainty: float | None = None
    a_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    ea_uncertainty_kj_mol: float | None = None

    tmin_k: float | None = None
    tmax_k: float | None = None

    degeneracy: float | None = None
    degeneracy_convention: KineticsDegeneracyConvention
    tunneling_model: TunnelingModel | None = None
    pressure_context: PressureContext | None = None
    pressure_bar: float | None = None

    has_literature: bool
    has_workflow_tool: bool
    has_transition_state_provenance: bool
    has_statmech_provenance: bool

    review_status: RecordReviewStatus
    created_at: datetime | None = None


class KineticsAnalyticsResponse(BaseModel):
    """Response envelope for ``/scientific/analytics/kinetics``."""

    request: AnalyticsRequestEcho
    review_summary: ReviewStatusSummary
    records: list[KineticsAnalyticsRecord]
    pagination: Pagination
    next_cursor: str | None = None
    watermark: WatermarkEcho | None = None


# ---------------------------------------------------------------------------
# Thermo
# ---------------------------------------------------------------------------


class ThermoAnalyticsRequest(AnalyticsPagingRequest):
    """Filters for ``/scientific/analytics/thermo``. All AND-combine."""

    scientific_origin: ScientificOriginKind | None = None
    phase: PhaseKind | None = None
    model_kind: ThermoModelKind | None = None

    reference_pressure_min_bar: float | None = None
    reference_pressure_max_bar: float | None = None

    h298_min_kj_mol: float | None = None
    h298_max_kj_mol: float | None = None
    s298_min_j_mol_k: float | None = None
    s298_max_j_mol_k: float | None = None
    enthalpy_formation_0k_min_kj_mol: float | None = None
    enthalpy_formation_0k_max_kj_mol: float | None = None

    has_uncertainty: bool | None = None
    h298_uncertainty_min_kj_mol: float | None = None
    h298_uncertainty_max_kj_mol: float | None = None

    has_literature: bool | None = None
    workflow_tool: str | None = Field(
        default=None, max_length=MAX_WORKFLOW_TOOL_LENGTH
    )

    has_statmech_provenance: bool | None = None


class ThermoAnalyticsRecord(BaseModel):
    """One thermo row, flattened to its quantitative columns."""

    thermo_id: int
    thermo_ref: str
    species_entry_id: int
    species_entry_ref: str

    scientific_origin: ScientificOriginKind
    model_kind: ThermoModelKind | None = None
    phase: PhaseKind | None = None
    reference_pressure_bar: float | None = None

    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None
    enthalpy_formation_0k_kj_mol: float | None = None

    h298_uncertainty_kj_mol: float | None = None
    s298_uncertainty_j_mol_k: float | None = None
    enthalpy_formation_0k_uncertainty_kj_mol: float | None = None

    tmin_k: float | None = None
    tmax_k: float | None = None

    has_literature: bool
    has_workflow_tool: bool
    has_statmech_provenance: bool

    review_status: RecordReviewStatus
    created_at: datetime | None = None


class ThermoAnalyticsResponse(BaseModel):
    """Response envelope for ``/scientific/analytics/thermo``."""

    request: AnalyticsRequestEcho
    review_summary: ReviewStatusSummary
    records: list[ThermoAnalyticsRecord]
    pagination: Pagination
    next_cursor: str | None = None
    watermark: WatermarkEcho | None = None


# ---------------------------------------------------------------------------
# Statmech
# ---------------------------------------------------------------------------


class StatmechAnalyticsRequest(AnalyticsPagingRequest):
    """Filters for ``/scientific/analytics/statmech``. All AND-combine.

    ``external_symmetry`` and ``optical_isomers`` are exact-match integers,
    not ranges: they are counts with physical meaning (a symmetry number of
    3 is not "between 2 and 4"), and a range over them would invite nonsense
    queries.
    """

    scientific_origin: ScientificOriginKind | None = None
    external_symmetry: int | None = Field(default=None, ge=1)
    is_linear: bool | None = None
    point_group: str | None = Field(
        default=None, max_length=MAX_POINT_GROUP_LENGTH
    )
    statmech_treatment: StatmechTreatmentKind | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    optical_isomers: int | None = Field(default=None, ge=1)

    rotational_constant_a_min_cm1: float | None = None
    rotational_constant_a_max_cm1: float | None = None
    rotational_constant_b_min_cm1: float | None = None
    rotational_constant_b_max_cm1: float | None = None
    rotational_constant_c_min_cm1: float | None = None
    rotational_constant_c_max_cm1: float | None = None

    has_frequency_scale_factor: bool | None = None
    has_torsions: bool | None = None
    has_electronic_levels: bool | None = None
    electronic_level_count_min: int | None = Field(default=None, ge=0)
    electronic_level_count_max: int | None = Field(default=None, ge=0)


class StatmechAnalyticsRecord(BaseModel):
    """One statmech row, flattened to its quantitative columns."""

    statmech_id: int
    statmech_ref: str
    species_entry_id: int | None = None
    species_entry_ref: str | None = None
    transition_state_entry_id: int | None = None
    transition_state_entry_ref: str | None = None

    scientific_origin: ScientificOriginKind
    external_symmetry: int | None = None
    is_linear: bool | None = None
    point_group: str | None = None
    statmech_treatment: StatmechTreatmentKind | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    optical_isomers: int | None = None

    rotational_constant_a_cm1: float | None = None
    rotational_constant_b_cm1: float | None = None
    rotational_constant_c_cm1: float | None = None

    has_frequency_scale_factor: bool
    torsion_count: int
    electronic_level_count: int

    review_status: RecordReviewStatus
    created_at: datetime | None = None


class StatmechAnalyticsResponse(BaseModel):
    """Response envelope for ``/scientific/analytics/statmech``."""

    request: AnalyticsRequestEcho
    review_summary: ReviewStatusSummary
    records: list[StatmechAnalyticsRecord]
    pagination: Pagination
    next_cursor: str | None = None
    watermark: WatermarkEcho | None = None


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------


class CalculationAnalyticsRequest(AnalyticsPagingRequest):
    """Filters for ``/scientific/analytics/calculations``. All AND-combine.

    The numeric axes live on the per-type result tables
    (``calc_sp_result``, ``calc_opt_result``, ``calc_freq_result``) and on
    the two diagnostic tables. Supplying such a filter joins that table
    inner, so it also acts as a presence filter — a calculation with no
    frequency result cannot match ``zpe_min_hartree``. That is the intended
    reading for dataset construction and is stated in the spec rather than
    left to be discovered.
    """

    calculation_type: CalculationType | None = None

    electronic_energy_min_hartree: float | None = None
    electronic_energy_max_hartree: float | None = None
    zpe_min_hartree: float | None = None
    zpe_max_hartree: float | None = None

    n_imag: int | None = Field(default=None, ge=0)
    converged: bool | None = None

    t1_min: float | None = None
    t1_max: float | None = None
    d1_min: float | None = None
    d1_max: float | None = None
    s_squared_min: float | None = None
    s_squared_max: float | None = None

    method: str | None = Field(default=None, max_length=MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=MAX_BASIS_LENGTH)
    lot_ref: str | None = Field(default=None, max_length=MAX_PUBLIC_REF_LENGTH)
    software: str | None = Field(
        default=None, max_length=MAX_SOFTWARE_NAME_LENGTH
    )


class CalculationAnalyticsRecord(BaseModel):
    """One calculation row, flattened to its quantitative columns."""

    calculation_id: int
    calculation_ref: str

    calculation_type: CalculationType
    quality: CalculationQuality

    electronic_energy_hartree: float | None = None
    final_energy_hartree: float | None = None
    converged: bool | None = None
    zpe_hartree: float | None = None
    n_imag: int | None = None
    imag_freq_cm1: float | None = None

    t1_diagnostic: float | None = None
    d1_diagnostic: float | None = None
    s_squared: float | None = None
    s_squared_expected: float | None = None

    method: str | None = None
    basis: str | None = None
    level_of_theory_ref: str | None = None
    software: str | None = None
    software_version: str | None = None

    review_status: RecordReviewStatus
    created_at: datetime | None = None


class CalculationAnalyticsResponse(BaseModel):
    """Response envelope for ``/scientific/analytics/calculations``."""

    request: AnalyticsRequestEcho
    review_summary: ReviewStatusSummary
    records: list[CalculationAnalyticsRecord]
    pagination: Pagination
    next_cursor: str | None = None
    watermark: WatermarkEcho | None = None


__all__ = [
    "MAX_CURSOR_LENGTH",
    "MAX_POINT_GROUP_LENGTH",
    "AnalyticsPagingRequest",
    "AnalyticsRequestEcho",
    "CalculationAnalyticsRecord",
    "CalculationAnalyticsRequest",
    "CalculationAnalyticsResponse",
    "KineticsAnalyticsRecord",
    "KineticsAnalyticsRequest",
    "KineticsAnalyticsResponse",
    "StatmechAnalyticsRecord",
    "StatmechAnalyticsRequest",
    "StatmechAnalyticsResponse",
    "ThermoAnalyticsRecord",
    "ThermoAnalyticsRequest",
    "ThermoAnalyticsResponse",
    "WatermarkEcho",
]
