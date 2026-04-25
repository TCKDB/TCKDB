"""Transport upload workflow orchestrator."""

from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.calculation import Calculation
from app.db.models.transport import Transport
from app.schemas.entities.transport import TransportSourceCalculationCreate
from app.schemas.workflows.transport_upload import TransportUploadRequest
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)
from app.services.species_resolution import resolve_species_entry
from app.services.transport_resolution import resolve_and_create_transport


def _assert_calculation_owned_by(
    calculation: Calculation,
    *,
    species_entry_id: int,
    context: str,
) -> None:
    """Defensive owner-consistency check for a resolved source calculation.

    Supporting calculations attached to a transport record must belong to
    the same species entry as the transport target; otherwise the
    provenance link would be scientifically meaningless.

    :raises ValueError: if the calculation does not belong to
        ``species_entry_id``.
    """
    if calculation.species_entry_id != species_entry_id:
        raise ValueError(
            f"{context}: calculation id={calculation.id} belongs to "
            f"species_entry_id={calculation.species_entry_id}, not to the "
            f"transport target species_entry_id={species_entry_id}."
        )


def persist_transport_upload(
    session: Session,
    request: TransportUploadRequest,
    *,
    created_by: int | None = None,
) -> Transport:
    """Persist a complete standalone transport upload workflow.

    Resolves the species entry, persists any inline supporting
    calculations, resolves provenance references, and creates a single
    ``Transport`` row with attached ``transport_source_calculation``
    links via the shared :func:`resolve_and_create_transport` service
    helper.

    Transport is append-only: multiple uploads against the same species
    entry produce independent rows.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing transport upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Transport`` row.
    :raises ValueError: If a resolved supporting calculation does not
        belong to the transport target's species entry.
    """
    species_entry = resolve_species_entry(
        session, request.species_entry, created_by=created_by
    )

    # Persist inline supporting calculations scoped to the target species
    # entry. Owner-consistency is enforced by construction but also
    # double-checked below to guard any future path that reuses existing
    # calculations.
    calculations_by_key: dict[str, Calculation] = {}
    for calc_in in request.calculations:
        calc_row = resolve_and_persist_calculation_with_results(
            session,
            calc_in.calculation,
            species_entry_id=species_entry.id,
            created_by=created_by,
        )
        _assert_calculation_owned_by(
            calc_row,
            species_entry_id=species_entry.id,
            context=f"transport calculation '{calc_in.key}'",
        )
        calculations_by_key[calc_in.key] = calc_row

    resolved_source_calcs = [
        TransportSourceCalculationCreate(
            calculation_id=calculations_by_key[sc.calculation_key].id,
            role=sc.role,
        )
        for sc in request.source_calculations
    ]

    transport = resolve_and_create_transport(
        session,
        request,
        species_entry_id=species_entry.id,
        source_calculations=resolved_source_calcs,
        created_by=created_by,
    )

    session.flush()
    return transport
