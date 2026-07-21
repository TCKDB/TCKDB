"""Read schemas for /api/v1/scientific/reaction-entries/{id}/kinetics.

See docs/specs/read_api_mvp.md §Endpoint 3.

Provenance keys are always present (Phase 2.2). TS-chain fields are populated
for TS-backed computational kinetics and ``null`` for non-TS-backed records
(experimental, estimated, imported, fitted, network-derived, literature-derived).
"""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import (
    ArrheniusAUnits,
    KineticsDirection,
    KineticsModelKind,
    KineticsUncertaintyKind,
    PressureContext,
    RecordReviewStatus,
    ScientificOriginKind,
)
from app.schemas.reads.scientific_assessment import PublicAssessmentSummary
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
    pressure_bar: float | None = Field(
        default=None,
        gt=0,
        description="Requested pressure in bar.",
    )
    pressure: float | None = Field(
        default=None,
        gt=0,
        deprecated=True,
        description="Deprecated alias for pressure_bar; retained for one release.",
    )
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

    @model_validator(mode="after")
    def _resolve_pressure_alias(self) -> Self:
        pressure_alias = self.__dict__.get("pressure")
        if self.pressure_bar is not None and pressure_alias is not None:
            if not math.isclose(
                self.pressure_bar,
                pressure_alias,
                rel_tol=1.0e-12,
                abs_tol=1.0e-12,
            ):
                raise ValueError(
                    "pressure_alias_conflict: pressure_bar and deprecated "
                    "pressure must agree."
                )
        elif self.pressure_bar is None:
            self.pressure_bar = pressure_alias
        return self


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


class MultiArrheniusTerm(BaseModel):
    """One modified-Arrhenius term of a sum-of-Arrhenius (DUPLICATE) rate.

    DR-0036: for ``model_kind == 'multi_arrhenius'`` records, the rate
    coefficient is the sum over these terms; the top-level ``parameters``
    block is empty (scalar A/n/Ea are null on the parent).
    """

    entry_index: int
    A: float
    A_units: ArrheniusAUnits | None = None
    n: float | None = None
    Ea_kj_mol: float | None = None


class KineticsUncertainty(BaseModel):
    """Uncertainty block — always returned, may have all-null entries."""

    A_uncertainty: float | None = None
    A_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    Ea_uncertainty_kj_mol: float | None = None


class PlogEntryBlock(BaseModel):
    """One pressure entry of a standalone PLOG rate (DR-0032 Part C).

    k(T,P) is interpolated in log P between bracketing entries; each entry is
    a modified-Arrhenius set at a fixed pressure. Mirrors the ``kinetics_plog``
    ORM child. Entries are returned ordered by ``entry_index``.
    """

    entry_index: int
    pressure_bar: float
    A: float
    A_units: ArrheniusAUnits | None = None
    n: float | None = None
    Ea_kj_mol: float | None = None


class ChebyshevBlock(BaseModel):
    """Chebyshev-polynomial k(T,P) surface (DR-0032 Part C).

    Mirrors the ``kinetics_chebyshev`` ORM child: the T/P validity domain and
    the ``n_temperature`` x ``n_pressure`` coefficient matrix (list of rows).

    Log basis: the coefficients follow the CHEMKIN base-10 convention — the
    surface expands ``log10 k`` in the Chebyshev basis over reduced T/P. This
    matches how they are stored and how ``chemkin_serialize.py`` re-emits them
    into the ``CHEB`` card, so a consumer reconstructing k must exponentiate
    base-10 (``k = 10 ** value``).
    """

    n_temperature: int
    n_pressure: int
    tmin_k: float | None = None
    tmax_k: float | None = None
    pmin_bar: float | None = None
    pmax_bar: float | None = None
    coefficients: list[list[float]]


class FalloffBlock(BaseModel):
    """Pressure-dependent falloff parameters (DR-0032 Part B).

    The high-pressure-limit (k∞) Arrhenius lives in the parent record's
    ``parameters`` block; this block carries the low-pressure-limit (k0)
    Arrhenius and the broadening coefficients. ``kind`` echoes the parent
    ``model_kind`` (``lindemann`` / ``troe`` / ``sri``), which selects the
    meaningful broadening columns: Lindemann uses none, Troe uses ``troe_*``,
    SRI uses ``sri_*``.
    """

    kind: KineticsModelKind
    low_A: float
    low_A_units: ArrheniusAUnits | None = None
    low_n: float | None = None
    low_Ea_kj_mol: float | None = None

    troe_alpha: float | None = None
    troe_t3: float | None = None
    troe_t1: float | None = None
    troe_t2: float | None = None

    sri_a: float | None = None
    sri_b: float | None = None
    sri_c: float | None = None
    sri_d: float | None = None
    sri_e: float | None = None


class ThirdBodyEfficiencyBlock(BaseModel):
    """One per-collider third-body efficiency (falloff / third-body rate).

    The collider is exposed as its graph-level species public ref
    (``collider_ref``), never the raw ``collider_species_id`` PK.
    ``efficiency`` scales the effective bath-gas concentration [M].
    """

    collider_ref: str
    efficiency: float


class PressureCoverage(BaseModel):
    """Why a returned rate is applicable at the requested pressure.

    Records with indeterminate or incompatible pressure semantics are filtered
    out, so ``applies_at_requested_pressure`` is always true when this block is
    present. ``basis`` distinguishes exact-pressure, bounded-surface,
    functional pressure-dependence, and pressure-independent records.
    """

    requested_pressure_bar: float
    applies_at_requested_pressure: Literal[True] = True
    basis: Literal[
        "pressure_independent",
        "exact_pressure",
        "bounded_pressure_surface",
        "pressure_dependent_model",
    ]
    record_pressure_bar: float | None = None
    record_pressure_min_bar: float | None = None
    record_pressure_max_bar: float | None = None


class ReactionPathDegeneracy(BaseModel):
    """Stored path degeneracy plus the rate-coefficient convention.

    TCKDB returns the reported/fitted rate coefficient unchanged. A stored
    degeneracy is metadata already incorporated in that rate; consumers must
    not multiply the rate a second time.
    """

    value: float
    reported_rate_coefficient_includes_degeneracy: Literal[True] = True
    apply_to_rate_coefficient: Literal[False] = False


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
    # DR-0036 bridge: the pressure-dependent network counterpart of this
    # reaction-level fit, if one has been linked. ``null`` otherwise.
    network_kinetics_id: int | None = None
    network_kinetics_ref: str | None = None


class KineticsRecord(BaseModel):
    """One kinetics record returned by the kinetics endpoint.

    Phase B: ``kinetics_ref`` is the public stable handle alongside
    ``kinetics_id``.
    """

    kinetics_id: int
    kinetics_ref: str
    scientific_origin: ScientificOriginKind
    model_kind: KineticsModelKind
    direction: KineticsDirection | None = None
    review: RecordReviewBadge
    parameters: ArrheniusParameters
    # DR-0036: populated only for ``multi_arrhenius`` records — the summed
    # modified-Arrhenius terms. ``null`` for every other model kind.
    multi_arrhenius: list[MultiArrheniusTerm] | None = None
    tunneling_model: str | None = None
    # Pressure-dependent / third-body forms (DR-0032). All ``None`` for a
    # plain Arrhenius record; each block is populated only when its data is
    # present on the underlying ``kinetics`` row.
    is_third_body: bool = False
    pressure_context: PressureContext | None = None
    pressure_bar: float | None = None
    pressure_coverage: PressureCoverage | None = None
    reaction_path_degeneracy: ReactionPathDegeneracy | None = None
    plog_entries: list[PlogEntryBlock] | None = None
    chebyshev: ChebyshevBlock | None = None
    falloff: FalloffBlock | None = None
    third_body_efficiencies: list[ThirdBodyEfficiencyBlock] | None = None
    uncertainty: KineticsUncertainty
    temperature_coverage: TemperatureCoverage | None = None
    evidence_completeness: EvidenceCompletenessBreakdown
    provenance: KineticsProvenance
    trust: TrustFragment | None = None
    assessments: PublicAssessmentSummary | None = None


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
