"""GET /api/v1/scientific/species-entries/{species_entry_ref}/conformer-usage.

Which thermo and kinetics records name each conformer group of one species entry. Public ref only; read in a snapshot.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.api.deps import get_snapshot_db
from app.api.server_faults import ServerFaultRoute
from app.schemas.reads.scientific_conformer_usage import ConformerUsageResponse
from app.services.scientific_read.conformer_usage import get_conformer_usage
from app.services.scientific_read.internal_ids import apply_internal_ids_visibility
from app.services.structure_selection.public import resolve_species_entry_ref

router = APIRouter(route_class=ServerFaultRoute, prefix="/species-entries")


@router.get(
    "/{species_entry_ref}/conformer-usage",
    response_model=ConformerUsageResponse,
    summary="Which thermo and kinetics records use each conformer group of a species entry",
)
def species_entry_conformer_usage(
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> ConformerUsageResponse:
    """Thermo records declared on one conformer group, kinetics assignments that select one, and ensemble thermo.

    Only declared links are listed; a record with no declared target appears nowhere. Groups nothing uses are omitted.
    Lists are ordered by ref and carry no rank. Records hidden by the read profile or the default review visibility
    are not listed.
    """
    entry_id = resolve_species_entry_ref(session, species_entry_ref)
    payload = get_conformer_usage(session, species_entry_id=entry_id, species_entry_ref=species_entry_ref)
    return apply_internal_ids_visibility(payload)
