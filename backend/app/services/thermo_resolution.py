"""Resolution service for thermo upload payloads."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.common import ThermoModelKind
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoNASA9Interval,
    ThermoPoint,
    ThermoSourceCalculation,
    ThermoWilhoit,
)
from app.schemas.entities.thermo import ThermoCreate
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software_release_ref


def infer_thermo_model_kind(
    *,
    model_kind: ThermoModelKind | None,
    has_nasa: bool,
    has_nasa9: bool,
    has_wilhoit: bool,
    has_points: bool,
) -> ThermoModelKind | None:
    """Resolve the effective thermo ``model_kind``.

    An explicit ``model_kind`` always wins. Otherwise infer from the
    populated representation: a fit takes precedence over tabulated points
    (nasa→nasa7, nasa9→nasa9, wilhoit→wilhoit), then points→tabulated; when
    nothing is populated the record is ``scalar``.

    The upload schema guarantees at most one *fit* block is populated (points
    may accompany a fit as auxiliary evidence) and that an explicit
    ``model_kind`` agrees with the primary representation, so the inference
    here is unambiguous.
    """
    if model_kind is not None:
        return model_kind
    if has_nasa:
        return ThermoModelKind.nasa7
    if has_nasa9:
        return ThermoModelKind.nasa9
    if has_wilhoit:
        return ThermoModelKind.wilhoit
    if has_points:
        return ThermoModelKind.tabulated
    return ThermoModelKind.scalar


def resolve_thermo_upload(
    session: Session,
    request: ThermoUploadRequest,
    *,
    species_entry_id: int,
) -> ThermoCreate:
    """Resolve workflow-facing thermo upload data into an internal create schema.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing thermo upload payload.
    :param species_entry_id: Resolved species-entry id.
    :returns: Internal ``ThermoCreate`` payload with resolved FK ids.
    """
    literature = (
        resolve_or_create_literature(session, request.literature)
        if request.literature is not None
        else None
    )
    software_release = (
        resolve_software_release_ref(session, request.software_release)
        if request.software_release is not None
        else None
    )
    workflow_tool_release = resolve_workflow_tool_release_ref(
        session, request.workflow_tool_release
    )

    model_kind = infer_thermo_model_kind(
        model_kind=request.model_kind,
        has_nasa=request.nasa is not None,
        has_nasa9=bool(request.nasa9_intervals),
        has_wilhoit=request.wilhoit is not None,
        has_points=bool(request.points),
    )

    return ThermoCreate(
        species_entry_id=species_entry_id,
        scientific_origin=request.scientific_origin,
        model_kind=model_kind,
        literature_id=literature.id if literature else None,
        software_release_id=software_release.id if software_release else None,
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release else None
        ),
        h298_kj_mol=request.h298_kj_mol,
        s298_j_mol_k=request.s298_j_mol_k,
        h298_uncertainty_kj_mol=request.h298_uncertainty_kj_mol,
        s298_uncertainty_j_mol_k=request.s298_uncertainty_j_mol_k,
        enthalpy_formation_0k_kj_mol=request.enthalpy_formation_0k_kj_mol,
        enthalpy_formation_0k_uncertainty_kj_mol=(
            request.enthalpy_formation_0k_uncertainty_kj_mol
        ),
        reference_pressure_bar=request.reference_pressure_bar,
        phase=request.phase,
        tmin_k=request.tmin_k,
        tmax_k=request.tmax_k,
        note=request.note,
        points=request.points,
        nasa=request.nasa,
        nasa9_intervals=request.nasa9_intervals,
        wilhoit=request.wilhoit,
        source_calculations=[],
        # statmech_id is resolved in the workflow (owner-consistency check)
        # and spliced onto the ThermoCreate there.
        statmech_id=None,
    )


def persist_thermo(
    session: Session,
    thermo_create: ThermoCreate,
    *,
    created_by: int | None = None,
) -> Thermo:
    """Persist a resolved thermo create payload.

    :param session: Active SQLAlchemy session.
    :param thermo_create: Internal resolved thermo payload.
    :param created_by: Optional application user id.
    :returns: Newly created ``Thermo`` row.
    """
    thermo = Thermo(
        species_entry_id=thermo_create.species_entry_id,
        scientific_origin=thermo_create.scientific_origin,
        model_kind=thermo_create.model_kind,
        literature_id=thermo_create.literature_id,
        workflow_tool_release_id=thermo_create.workflow_tool_release_id,
        software_release_id=thermo_create.software_release_id,
        statmech_id=thermo_create.statmech_id,
        h298_kj_mol=thermo_create.h298_kj_mol,
        s298_j_mol_k=thermo_create.s298_j_mol_k,
        h298_uncertainty_kj_mol=thermo_create.h298_uncertainty_kj_mol,
        s298_uncertainty_j_mol_k=thermo_create.s298_uncertainty_j_mol_k,
        enthalpy_formation_0k_kj_mol=thermo_create.enthalpy_formation_0k_kj_mol,
        enthalpy_formation_0k_uncertainty_kj_mol=(
            thermo_create.enthalpy_formation_0k_uncertainty_kj_mol
        ),
        reference_pressure_bar=thermo_create.reference_pressure_bar,
        phase=thermo_create.phase,
        tmin_k=thermo_create.tmin_k,
        tmax_k=thermo_create.tmax_k,
        note=thermo_create.note,
        created_by=created_by,
    )
    session.add(thermo)
    session.flush()

    for point in thermo_create.points:
        session.add(
            ThermoPoint(
                thermo_id=thermo.id,
                temperature_k=point.temperature_k,
                cp_j_mol_k=point.cp_j_mol_k,
                h_kj_mol=point.h_kj_mol,
                s_j_mol_k=point.s_j_mol_k,
                g_kj_mol=point.g_kj_mol,
            )
        )

    if thermo_create.nasa is not None:
        nasa = thermo_create.nasa
        session.add(
            ThermoNASA(
                thermo_id=thermo.id,
                t_low=nasa.t_low,
                t_mid=nasa.t_mid,
                t_high=nasa.t_high,
                a1=nasa.a1,
                a2=nasa.a2,
                a3=nasa.a3,
                a4=nasa.a4,
                a5=nasa.a5,
                a6=nasa.a6,
                a7=nasa.a7,
                b1=nasa.b1,
                b2=nasa.b2,
                b3=nasa.b3,
                b4=nasa.b4,
                b5=nasa.b5,
                b6=nasa.b6,
                b7=nasa.b7,
            )
        )

    for interval in thermo_create.nasa9_intervals:
        session.add(
            ThermoNASA9Interval(
                thermo_id=thermo.id,
                interval_index=interval.interval_index,
                t_min_k=interval.t_min_k,
                t_max_k=interval.t_max_k,
                a1=interval.a1,
                a2=interval.a2,
                a3=interval.a3,
                a4=interval.a4,
                a5=interval.a5,
                a6=interval.a6,
                a7=interval.a7,
                a8=interval.a8,
                a9=interval.a9,
            )
        )

    if thermo_create.wilhoit is not None:
        w = thermo_create.wilhoit
        session.add(
            ThermoWilhoit(
                thermo_id=thermo.id,
                cp0_j_mol_k=w.cp0_j_mol_k,
                cp_inf_j_mol_k=w.cp_inf_j_mol_k,
                b_k=w.b_k,
                a0=w.a0,
                a1=w.a1,
                a2=w.a2,
                a3=w.a3,
                h0_kj_mol=w.h0_kj_mol,
                s0_j_mol_k=w.s0_j_mol_k,
            )
        )

    for sc in thermo_create.source_calculations:
        session.add(
            ThermoSourceCalculation(
                thermo_id=thermo.id,
                calculation_id=sc.calculation_id,
                role=sc.role,
            )
        )

    if (
        thermo_create.points
        or thermo_create.nasa
        or thermo_create.nasa9_intervals
        or thermo_create.wilhoit
        or thermo_create.source_calculations
    ):
        session.flush()

    return thermo
