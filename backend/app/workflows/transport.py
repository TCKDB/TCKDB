"""Transport upload workflow orchestrator."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType
from app.db.models.transport import Transport
from app.schemas.entities.transport import TransportSourceCalculationCreate
from app.schemas.workflows.transport_upload import TransportUploadRequest
from app.services.calculation_ownership import (
    W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH,
    assert_calculation_owned_by,
)
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)
from app.services.local_key_resolution import resolve_calculation_key
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.species_resolution import resolve_species_entry
from app.services.transport_resolution import resolve_and_create_transport

logger = logging.getLogger(__name__)


def persist_transport_upload(
    session: Session,
    request: TransportUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
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
        assert_calculation_owned_by(
            calc_row,
            code=W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH,
            target="transport",
            context=f"transport calculation '{calc_in.key}'",
            species_entry_id=species_entry.id,
        )
        calculations_by_key[calc_in.key] = calc_row

    resolved_source_calcs = [
        TransportSourceCalculationCreate(
            calculation_id=resolve_calculation_key(
                sc.calculation_key,
                calculations_by_key,
                field=f"source_calculations[{index}].calculation_key",
            ).id,
            role=sc.role,
        )
        for index, sc in enumerate(request.source_calculations)
    ]

    transport = resolve_and_create_transport(
        session,
        request,
        species_entry_id=species_entry.id,
        source_calculations=resolved_source_calcs,
        created_by=created_by,
    )

    session.flush()

    targets = [
        RecordRef(SubmissionRecordType.transport, transport.id),
        RecordRef(SubmissionRecordType.species_entry, species_entry.id),
    ]
    targets.extend(
        RecordRef(SubmissionRecordType.calculation, c.id)
        for c in calculations_by_key.values()
    )
    apply_review_policy(
        session, targets=targets, policy=review_policy, created_by=created_by
    )

    return transport
