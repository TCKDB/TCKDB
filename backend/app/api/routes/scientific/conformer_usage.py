"""GET /api/v1/scientific/species-entries/{species_entry_ref}/conformer-usage.

Which thermo and kinetics records name each conformer group of one species entry. Public ref only; read in a snapshot.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_snapshot_db
from app.api.server_faults import ServerFaultRoute
from app.db.models.common import RecordReviewStatus
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
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
) -> ConformerUsageResponse:
    """Thermo records declared on one conformer group, kinetics assignments that select one, and ensemble thermo.

    Only declared links are listed; a record with no declared target appears nowhere. Groups nothing uses are omitted.
    Lists are ordered by ref and carry no rank. Rejected and deprecated records are hidden by default and counted in
    ``request.hidden_by_default``; ``include_rejected`` / ``include_deprecated`` / ``min_review_status`` work as on the
    thermo list, and the curated profile floor still applies. A group a merge emptied lists its own usage and names the
    group it was merged into; nothing is forwarded.
    """
    entry_id = resolve_species_entry_ref(session, species_entry_ref)
    payload = get_conformer_usage(
        session,
        species_entry_id=entry_id,
        species_entry_ref=species_entry_ref,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        min_review_status=min_review_status,
    )
    return apply_internal_ids_visibility(payload)
