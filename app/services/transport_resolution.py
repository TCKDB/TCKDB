"""Resolution service for transport upload payloads."""

from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.transport import Transport, TransportSourceCalculation
from app.schemas.entities.transport import TransportSourceCalculationCreate
from app.schemas.workflows.transport_upload import TransportUploadPayload
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software_release_ref


def resolve_and_create_transport(
    session: Session,
    payload: TransportUploadPayload,
    *,
    species_entry_id: int,
    source_calculations: list[TransportSourceCalculationCreate] | None = None,
    created_by: int | None = None,
) -> Transport:
    """Resolve provenance refs and create a transport record.

    This is the single place where transport rows are materialized from an
    upload payload. All callers (standalone transport upload, conformer
    upload with nested transport, network PDep species transport) route
    through here to keep transport persistence consistent and append-only.

    Always creates a new row — transport records are provenance-bearing
    scientific assertions, and multiple records per species entry are
    valid.

    :param session: Active SQLAlchemy session.
    :param payload: Upload-facing transport payload with provenance refs.
    :param species_entry_id: Resolved owner species-entry id.
    :param source_calculations: Optional pre-resolved source-calculation
        links to attach to the new transport row. Callers are responsible
        for resolving local keys to calculation ids before calling.
    :param created_by: Optional application user id for the created row.
    :returns: Newly created ``Transport`` row.
    """
    literature = (
        resolve_or_create_literature(session, payload.literature)
        if payload.literature is not None
        else None
    )
    software_release = (
        resolve_software_release_ref(session, payload.software_release)
        if payload.software_release is not None
        else None
    )
    workflow_tool_release = resolve_workflow_tool_release_ref(
        session, payload.workflow_tool_release
    )

    transport = Transport(
        species_entry_id=species_entry_id,
        scientific_origin=payload.scientific_origin,
        literature_id=literature.id if literature else None,
        software_release_id=software_release.id if software_release else None,
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release else None
        ),
        sigma_angstrom=payload.sigma_angstrom,
        epsilon_over_k_k=payload.epsilon_over_k_k,
        dipole_debye=payload.dipole_debye,
        polarizability_angstrom3=payload.polarizability_angstrom3,
        rotational_relaxation=payload.rotational_relaxation,
        note=payload.note,
        created_by=created_by,
    )
    session.add(transport)
    session.flush()

    if source_calculations:
        for sc in source_calculations:
            session.add(
                TransportSourceCalculation(
                    transport_id=transport.id,
                    calculation_id=sc.calculation_id,
                    role=sc.role,
                )
            )
        session.flush()

    return transport
