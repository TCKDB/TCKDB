"""GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import KineticsModelKind, RecordReviewStatus
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_kinetics import (
    KineticsReadRequest,
    ScientificReactionKineticsResponse,
)
from app.services.scientific_read.kinetics import get_reaction_kinetics

router = APIRouter(prefix="/reaction-entries")


@router.get(
    "/{reaction_entry_id}/kinetics",
    response_model=ScientificReactionKineticsResponse,
)
def reaction_kinetics(
    reaction_entry_id: int = Path(..., ge=1),
    session: Session = Depends(get_db),
    temperature_min: float | None = Query(None),
    temperature_max: float | None = Query(None),
    pressure: float | None = Query(None),
    model_kind: KineticsModelKind | None = Query(None),
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
) -> ScientificReactionKineticsResponse:
    """Return kinetics records for a reaction entry, sorted per D9.

    Path param is strictly ``reaction_entry.id`` — supplying a
    ``chem_reaction.id`` returns 404. Provenance keys are always present;
    TS-chain fields are populated only for TS-backed records (Phase 2.2).
    Default sort is the locked D9 chain. ``sort=`` is rejected (v0).
    See ``docs/specs/read_api_mvp.md`` §Endpoint 3.
    """
    request = KineticsReadRequest(
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        pressure=pressure,
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
    return get_reaction_kinetics(
        session, reaction_entry_id=reaction_entry_id, request=request
    )
