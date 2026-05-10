"""GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/full."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_provenance import (
    ReactionFullReadRequest,
    ReviewDetail,
    ScientificReactionFullResponse,
)
from app.services.scientific_read.provenance import get_reaction_full

router = APIRouter(prefix="/reaction-entries")


@router.get(
    "/{reaction_entry_id}/full",
    response_model=ScientificReactionFullResponse,
)
def reaction_full(
    reaction_entry_id: int = Path(..., ge=1),
    session: Session = Depends(get_db),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    include_review: ReviewDetail = Query(ReviewDetail.summary),
) -> ScientificReactionFullResponse:
    """Composite scientific read for a reaction entry.

    Joins species, kinetics, transition states, calculations, and review
    summary into one document. Top-level filters apply per joined sub-array
    (Phase 2.1). Non-TS-backed kinetics surface in ``kinetics`` with null
    TS-chain provenance fields (Phase 2.2). Sub-arrays sort deterministically;
    client-supplied ``sort=`` is rejected. See ``docs/specs/read_api_mvp.md``
    §Endpoint 5.
    """
    request = ReactionFullReadRequest(
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        include=parse_include(include),
        include_review=include_review,
    )
    return get_reaction_full(
        session, reaction_entry_id=reaction_entry_id, request=request
    )
