"""GET /api/v1/scientific/analytics/{kinetics,thermo,statmech,calculations}.

Four endpoints, deliberately. The alternative — adding numeric filters to the
~40 transactional searches — would have produced a surface with no bounded set
of query shapes to index or document. See
``backend/docs/specs/scientific_analytics_surface.md``.

GET only. The POST twin the chemistry-first searches carry exists because
reactant/product SMILES lists are awkward in a query string; analytics filters
are scalars, and a cursor is a short opaque token, so a body form would add
surface without answering a question.

Each handler is a thin translation into the service call and back through
:func:`~app.services.scientific_read.internal_ids.apply_internal_ids_visibility`
— the one boundary that both hides internal integer ids and stamps the
resolved read profile into the response envelope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import (
    CalculationType,
    KineticsDirection,
    KineticsModelKind,
    PhaseKind,
    PressureContext,
    RecordReviewStatus,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechTreatmentKind,
    ThermoModelKind,
    TunnelingModel,
)
from app.schemas.reads.scientific_analytics import (
    MAX_CURSOR_LENGTH,
    CalculationAnalyticsRequest,
    CalculationAnalyticsResponse,
    KineticsAnalyticsRequest,
    KineticsAnalyticsResponse,
    StatmechAnalyticsRequest,
    StatmechAnalyticsResponse,
    ThermoAnalyticsRequest,
    ThermoAnalyticsResponse,
)
from app.services.scientific_read.analytics import (
    search_calculation_analytics,
    search_kinetics_analytics,
    search_statmech_analytics,
    search_thermo_analytics,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)

router = APIRouter(prefix="/analytics", tags=["scientific-analytics"])

_CURSOR_DESCRIPTION = (
    "Opt-in keyset traversal. Pass the previous response's next_cursor to get "
    "the following page; the cursor pins the traversal to a snapshot of the "
    "records that existed when it began. Cannot be combined with a non-zero "
    "offset. The snapshot bounds insertions, not curation — a record whose "
    "review status changes mid-traversal can still enter or leave the visible "
    "set. For an immutable, citable set use a published dataset release."
)


@router.get("/kinetics", response_model=KineticsAnalyticsResponse)
def kinetics_analytics(
    session: Session = Depends(get_db),
    scientific_origin: ScientificOriginKind | None = Query(None),
    direction: KineticsDirection | None = Query(
        None, description="Stored direction of the fit (DR-0036)."
    ),
    model_kind: KineticsModelKind | None = Query(None),
    tunneling_model: TunnelingModel | None = Query(None),
    pressure_context: PressureContext | None = Query(None),
    degeneracy_min: float | None = Query(None),
    degeneracy_max: float | None = Query(None),
    pressure_min_bar: float | None = Query(None),
    pressure_max_bar: float | None = Query(None),
    a_min: float | None = Query(None),
    a_max: float | None = Query(None),
    n_min: float | None = Query(None),
    n_max: float | None = Query(None),
    ea_min_kj_mol: float | None = Query(None),
    ea_max_kj_mol: float | None = Query(None),
    has_uncertainty: bool | None = Query(None),
    ea_uncertainty_min_kj_mol: float | None = Query(None),
    ea_uncertainty_max_kj_mol: float | None = Query(None),
    temperature_min_k: float | None = Query(
        None,
        description=(
            "Coverage filter: the record's own tmin_k must be at or below "
            "this. Records with no stated range do not match."
        ),
    ),
    temperature_max_k: float | None = Query(
        None,
        description=(
            "Coverage filter: the record's own tmax_k must be at or above "
            "this. Records with no stated range do not match."
        ),
    ),
    has_literature: bool | None = Query(None),
    workflow_tool: str | None = Query(None),
    has_transition_state_provenance: bool | None = Query(None),
    has_statmech_provenance: bool | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(
        None, max_length=MAX_CURSOR_LENGTH, description=_CURSOR_DESCRIPTION
    ),
):
    """Quantitative kinetics search for dataset construction."""
    payload = search_kinetics_analytics(
        session,
        KineticsAnalyticsRequest(
            scientific_origin=scientific_origin,
            direction=direction,
            model_kind=model_kind,
            tunneling_model=tunneling_model,
            pressure_context=pressure_context,
            degeneracy_min=degeneracy_min,
            degeneracy_max=degeneracy_max,
            pressure_min_bar=pressure_min_bar,
            pressure_max_bar=pressure_max_bar,
            a_min=a_min,
            a_max=a_max,
            n_min=n_min,
            n_max=n_max,
            ea_min_kj_mol=ea_min_kj_mol,
            ea_max_kj_mol=ea_max_kj_mol,
            has_uncertainty=has_uncertainty,
            ea_uncertainty_min_kj_mol=ea_uncertainty_min_kj_mol,
            ea_uncertainty_max_kj_mol=ea_uncertainty_max_kj_mol,
            temperature_min_k=temperature_min_k,
            temperature_max_k=temperature_max_k,
            has_literature=has_literature,
            workflow_tool=workflow_tool,
            has_transition_state_provenance=has_transition_state_provenance,
            has_statmech_provenance=has_statmech_provenance,
            min_review_status=min_review_status,
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            sort=sort,
            include=parse_include(include),
            offset=offset,
            limit=limit,
            cursor=cursor,
        ),
    )
    return apply_internal_ids_visibility(payload)


@router.get("/thermo", response_model=ThermoAnalyticsResponse)
def thermo_analytics(
    session: Session = Depends(get_db),
    scientific_origin: ScientificOriginKind | None = Query(None),
    phase: PhaseKind | None = Query(None),
    model_kind: ThermoModelKind | None = Query(None),
    reference_pressure_min_bar: float | None = Query(None),
    reference_pressure_max_bar: float | None = Query(None),
    h298_min_kj_mol: float | None = Query(None),
    h298_max_kj_mol: float | None = Query(None),
    s298_min_j_mol_k: float | None = Query(None),
    s298_max_j_mol_k: float | None = Query(None),
    enthalpy_formation_0k_min_kj_mol: float | None = Query(None),
    enthalpy_formation_0k_max_kj_mol: float | None = Query(None),
    has_uncertainty: bool | None = Query(None),
    h298_uncertainty_min_kj_mol: float | None = Query(None),
    h298_uncertainty_max_kj_mol: float | None = Query(None),
    has_literature: bool | None = Query(None),
    workflow_tool: str | None = Query(None),
    has_statmech_provenance: bool | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(
        None, max_length=MAX_CURSOR_LENGTH, description=_CURSOR_DESCRIPTION
    ),
):
    """Quantitative thermochemistry search for dataset construction."""
    payload = search_thermo_analytics(
        session,
        ThermoAnalyticsRequest(
            scientific_origin=scientific_origin,
            phase=phase,
            model_kind=model_kind,
            reference_pressure_min_bar=reference_pressure_min_bar,
            reference_pressure_max_bar=reference_pressure_max_bar,
            h298_min_kj_mol=h298_min_kj_mol,
            h298_max_kj_mol=h298_max_kj_mol,
            s298_min_j_mol_k=s298_min_j_mol_k,
            s298_max_j_mol_k=s298_max_j_mol_k,
            enthalpy_formation_0k_min_kj_mol=enthalpy_formation_0k_min_kj_mol,
            enthalpy_formation_0k_max_kj_mol=enthalpy_formation_0k_max_kj_mol,
            has_uncertainty=has_uncertainty,
            h298_uncertainty_min_kj_mol=h298_uncertainty_min_kj_mol,
            h298_uncertainty_max_kj_mol=h298_uncertainty_max_kj_mol,
            has_literature=has_literature,
            workflow_tool=workflow_tool,
            has_statmech_provenance=has_statmech_provenance,
            min_review_status=min_review_status,
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            sort=sort,
            include=parse_include(include),
            offset=offset,
            limit=limit,
            cursor=cursor,
        ),
    )
    return apply_internal_ids_visibility(payload)


@router.get("/statmech", response_model=StatmechAnalyticsResponse)
def statmech_analytics(
    session: Session = Depends(get_db),
    scientific_origin: ScientificOriginKind | None = Query(None),
    external_symmetry: int | None = Query(None, ge=1),
    is_linear: bool | None = Query(None),
    point_group: str | None = Query(None),
    statmech_treatment: StatmechTreatmentKind | None = Query(None),
    rigid_rotor_kind: RigidRotorKind | None = Query(None),
    optical_isomers: int | None = Query(None, ge=1),
    rotational_constant_a_min_cm1: float | None = Query(None),
    rotational_constant_a_max_cm1: float | None = Query(None),
    rotational_constant_b_min_cm1: float | None = Query(None),
    rotational_constant_b_max_cm1: float | None = Query(None),
    rotational_constant_c_min_cm1: float | None = Query(None),
    rotational_constant_c_max_cm1: float | None = Query(None),
    has_frequency_scale_factor: bool | None = Query(None),
    has_torsions: bool | None = Query(None),
    has_electronic_levels: bool | None = Query(None),
    electronic_level_count_min: int | None = Query(None, ge=0),
    electronic_level_count_max: int | None = Query(None, ge=0),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(
        None, max_length=MAX_CURSOR_LENGTH, description=_CURSOR_DESCRIPTION
    ),
):
    """Quantitative statistical-mechanics search for dataset construction."""
    payload = search_statmech_analytics(
        session,
        StatmechAnalyticsRequest(
            scientific_origin=scientific_origin,
            external_symmetry=external_symmetry,
            is_linear=is_linear,
            point_group=point_group,
            statmech_treatment=statmech_treatment,
            rigid_rotor_kind=rigid_rotor_kind,
            optical_isomers=optical_isomers,
            rotational_constant_a_min_cm1=rotational_constant_a_min_cm1,
            rotational_constant_a_max_cm1=rotational_constant_a_max_cm1,
            rotational_constant_b_min_cm1=rotational_constant_b_min_cm1,
            rotational_constant_b_max_cm1=rotational_constant_b_max_cm1,
            rotational_constant_c_min_cm1=rotational_constant_c_min_cm1,
            rotational_constant_c_max_cm1=rotational_constant_c_max_cm1,
            has_frequency_scale_factor=has_frequency_scale_factor,
            has_torsions=has_torsions,
            has_electronic_levels=has_electronic_levels,
            electronic_level_count_min=electronic_level_count_min,
            electronic_level_count_max=electronic_level_count_max,
            min_review_status=min_review_status,
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            sort=sort,
            include=parse_include(include),
            offset=offset,
            limit=limit,
            cursor=cursor,
        ),
    )
    return apply_internal_ids_visibility(payload)


@router.get("/calculations", response_model=CalculationAnalyticsResponse)
def calculation_analytics(
    session: Session = Depends(get_db),
    calculation_type: CalculationType | None = Query(None),
    electronic_energy_min_hartree: float | None = Query(None),
    electronic_energy_max_hartree: float | None = Query(None),
    zpe_min_hartree: float | None = Query(None),
    zpe_max_hartree: float | None = Query(None),
    n_imag: int | None = Query(None, ge=0),
    converged: bool | None = Query(None),
    t1_min: float | None = Query(None),
    t1_max: float | None = Query(None),
    d1_min: float | None = Query(None),
    d1_max: float | None = Query(None),
    s_squared_min: float | None = Query(None),
    s_squared_max: float | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    lot_ref: str | None = Query(None),
    software: str | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(
        None, max_length=MAX_CURSOR_LENGTH, description=_CURSOR_DESCRIPTION
    ),
):
    """Quantitative calculation search for dataset construction."""
    payload = search_calculation_analytics(
        session,
        CalculationAnalyticsRequest(
            calculation_type=calculation_type,
            electronic_energy_min_hartree=electronic_energy_min_hartree,
            electronic_energy_max_hartree=electronic_energy_max_hartree,
            zpe_min_hartree=zpe_min_hartree,
            zpe_max_hartree=zpe_max_hartree,
            n_imag=n_imag,
            converged=converged,
            t1_min=t1_min,
            t1_max=t1_max,
            d1_min=d1_min,
            d1_max=d1_max,
            s_squared_min=s_squared_min,
            s_squared_max=s_squared_max,
            method=method,
            basis=basis,
            lot_ref=lot_ref,
            software=software,
            min_review_status=min_review_status,
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            sort=sort,
            include=parse_include(include),
            offset=offset,
            limit=limit,
            cursor=cursor,
        ),
    )
    return apply_internal_ids_visibility(payload)
