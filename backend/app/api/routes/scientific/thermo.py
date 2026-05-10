"""GET /api/v1/scientific/species-entries/{species_entry_id}/thermo."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_thermo import (
    ScientificSpeciesThermoResponse,
    ThermoModelKindQuery,
    ThermoReadRequest,
)
from app.services.scientific_read.thermo import get_species_thermo

router = APIRouter(prefix="/species-entries")


@router.get(
    "/{species_entry_id}/thermo",
    response_model=ScientificSpeciesThermoResponse,
)
def species_thermo(
    species_entry_id: int = Path(..., ge=1),
    session: Session = Depends(get_db),
    temperature_min: float | None = Query(None),
    temperature_max: float | None = Query(None),
    model_kind: ThermoModelKindQuery | None = Query(None),
    level_of_theory_id: int | None = Query(None),
    software: str | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    collapse: CollapseMode = Query(CollapseMode.all),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificSpeciesThermoResponse:
    """Return thermo records for a species entry, sorted per L3.

    Path param is strictly ``species_entry.id`` — supplying a ``species.id``
    returns 404. ``sort=`` is rejected (v0). See ``docs/specs/read_api_mvp.md``
    §Endpoint 4.
    """
    request = ThermoReadRequest(
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        model_kind=model_kind,
        level_of_theory_id=level_of_theory_id,
        software=software,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        collapse=collapse,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return get_species_thermo(
        session, species_entry_id=species_entry_id, request=request
    )
