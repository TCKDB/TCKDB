from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.chemistry.units import convert_ea_to_kj_mol
from app.db.models.kinetics import Kinetics
from app.schemas.entities.kinetics import KineticsCreate
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software_release_ref


def resolve_kinetics_upload(
    session: Session,
    request: KineticsUploadRequest,
    *,
    reaction_entry_id: int,
) -> KineticsCreate:
    """Resolve workflow-facing kinetics upload data into an internal create schema.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing kinetics upload payload.
    :param reaction_entry_id: Resolved reaction-entry id from backend workflow logic.
    :returns: Internal ``KineticsCreate`` payload with resolved foreign-key ids.
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
        session,
        request.workflow_tool_release,
    )

    return KineticsCreate(
        reaction_entry_id=reaction_entry_id,
        scientific_origin=request.scientific_origin,
        model_kind=request.model_kind,
        literature_id=literature.id if literature is not None else None,
        software_release_id=(
            software_release.id if software_release is not None else None
        ),
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release is not None else None
        ),
        a=request.a,
        a_units=request.a_units,
        n=request.n,
        ea_kj_mol=(
            convert_ea_to_kj_mol(request.reported_ea, request.reported_ea_units)
            if request.reported_ea is not None
            else None
        ),
        a_uncertainty=request.a_uncertainty,
        n_uncertainty=request.n_uncertainty,
        ea_uncertainty_kj_mol=(
            convert_ea_to_kj_mol(request.d_reported_ea, request.reported_ea_units)
            if request.d_reported_ea is not None
            else None
        ),
        tmin_k=request.tmin_k,
        tmax_k=request.tmax_k,
        degeneracy=request.degeneracy,
        tunneling_model=request.tunneling_model,
        note=request.note,
        source_calculations=[],
    )


def persist_kinetics(
    session: Session,
    kinetics_create: KineticsCreate,
    *,
    created_by: int | None = None,
) -> Kinetics:
    """Persist a resolved kinetics create payload.

    :param session: Active SQLAlchemy session.
    :param kinetics_create: Internal resolved kinetics payload.
    :param created_by: Optional application user id for the created row.
    :returns: Newly created ``Kinetics`` row.
    """

    kinetics = Kinetics(
        reaction_entry_id=kinetics_create.reaction_entry_id,
        scientific_origin=kinetics_create.scientific_origin,
        model_kind=kinetics_create.model_kind,
        literature_id=kinetics_create.literature_id,
        workflow_tool_release_id=kinetics_create.workflow_tool_release_id,
        software_release_id=kinetics_create.software_release_id,
        a=kinetics_create.a,
        a_units=kinetics_create.a_units,
        n=kinetics_create.n,
        ea_kj_mol=kinetics_create.ea_kj_mol,
        a_uncertainty=kinetics_create.a_uncertainty,
        n_uncertainty=kinetics_create.n_uncertainty,
        ea_uncertainty_kj_mol=kinetics_create.ea_uncertainty_kj_mol,
        tmin_k=kinetics_create.tmin_k,
        tmax_k=kinetics_create.tmax_k,
        degeneracy=kinetics_create.degeneracy,
        tunneling_model=kinetics_create.tunneling_model,
        note=kinetics_create.note,
        created_by=created_by,
    )
    session.add(kinetics)
    session.flush()
    return kinetics
