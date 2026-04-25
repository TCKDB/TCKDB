"""Resolution service for thermo upload payloads."""

from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoPoint,
    ThermoSourceCalculation,
)
from app.schemas.entities.thermo import ThermoCreate
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software_release_ref


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

    return ThermoCreate(
        species_entry_id=species_entry_id,
        scientific_origin=request.scientific_origin,
        literature_id=literature.id if literature else None,
        software_release_id=software_release.id if software_release else None,
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release else None
        ),
        h298_kj_mol=request.h298_kj_mol,
        s298_j_mol_k=request.s298_j_mol_k,
        h298_uncertainty_kj_mol=request.h298_uncertainty_kj_mol,
        s298_uncertainty_j_mol_k=request.s298_uncertainty_j_mol_k,
        tmin_k=request.tmin_k,
        tmax_k=request.tmax_k,
        note=request.note,
        points=request.points,
        nasa=request.nasa,
        source_calculations=[],
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
        literature_id=thermo_create.literature_id,
        workflow_tool_release_id=thermo_create.workflow_tool_release_id,
        software_release_id=thermo_create.software_release_id,
        h298_kj_mol=thermo_create.h298_kj_mol,
        s298_j_mol_k=thermo_create.s298_j_mol_k,
        h298_uncertainty_kj_mol=thermo_create.h298_uncertainty_kj_mol,
        s298_uncertainty_j_mol_k=thermo_create.s298_uncertainty_j_mol_k,
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

    for sc in thermo_create.source_calculations:
        session.add(
            ThermoSourceCalculation(
                thermo_id=thermo.id,
                calculation_id=sc.calculation_id,
                role=sc.role,
            )
        )

    if thermo_create.points or thermo_create.nasa or thermo_create.source_calculations:
        session.flush()

    return thermo
