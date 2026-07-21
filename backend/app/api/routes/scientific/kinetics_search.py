"""GET + POST /api/v1/scientific/kinetics/search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import prepare_assessment_response
from app.db.models.common import KineticsModelKind, RecordReviewStatus
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_kinetics_search import (
    KineticsSearchRequest,
    ScientificKineticsSearchResponse,
)
from app.schemas.reads.scientific_reactions import ReactionDirectionQuery
from app.services.scientific_read.kinetics_search import search_kinetics
from app.services.scientific_read.public_assessments import (
    attach_kinetics_assessments,
)

router = APIRouter(prefix="/kinetics")

_POST_ALLOWED_QS_KEYS: set[str] = set()


@router.get("/search", response_model=ScientificKineticsSearchResponse)
def kinetics_search_get(
    session: Session = Depends(get_db),
    reactants: list[str] | None = Query(None),
    products: list[str] | None = Query(None),
    direction: ReactionDirectionQuery = Query(ReactionDirectionQuery.either),
    family: str | None = Query(None),
    reaction_ref: str | None = Query(None),
    reaction_entry_ref: str | None = Query(None),
    temperature_min: float | None = Query(None),
    temperature_max: float | None = Query(None),
    pressure_bar: float | None = Query(
        None,
        gt=0,
        allow_inf_nan=False,
        description="Requested pressure in bar.",
    ),
    pressure: float | None = Query(
        None,
        gt=0,
        allow_inf_nan=False,
        deprecated=True,
        description="Deprecated alias for pressure_bar; retained for one release.",
    ),
    model_kind: KineticsModelKind | None = Query(None),
    level_of_theory_id: int | None = Query(None),
    level_of_theory_ref: str | None = Query(None),
    software: str | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    collapse: CollapseMode = Query(CollapseMode.all),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificKineticsSearchResponse:
    """Chemistry-first kinetics search by reactants/products.

    Repeated ``reactants=`` and ``products=`` are accepted on the GET form.
    For complex reactant/product lists (especially with bracket-heavy
    SMILES), prefer the POST form. Returns kinetics records with the
    resolved reaction/reaction_entry identity attached.
    """
    request = KineticsSearchRequest(
        reactants=reactants or [],
        products=products or [],
        direction=direction,
        family=family,
        reaction_ref=reaction_ref,
        reaction_entry_ref=reaction_entry_ref,
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        pressure_bar=pressure_bar,
        pressure=pressure,
        model_kind=model_kind,
        level_of_theory_id=level_of_theory_id,
        level_of_theory_ref=level_of_theory_ref,
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
    payload = search_kinetics(session, request)
    return prepare_assessment_response(
        session,
        payload,
        attach_assessments=attach_kinetics_assessments,
    )


@router.post("/search", response_model=ScientificKineticsSearchResponse)
def kinetics_search_post(
    request: Request,
    body: KineticsSearchRequest,
    session: Session = Depends(get_db),
) -> ScientificKineticsSearchResponse:
    """JSON-body variant for structured reactant/product queries.

    All search fields, filters, includes, collapse, offset, and limit live
    in the body. Query-string parameters are rejected. Body field ``sort``
    is rejected by the service layer (v0 sort policy).
    """
    forbidden = set(request.query_params.keys()) - _POST_ALLOWED_QS_KEYS
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                "post_search_fields_must_be_in_body: query-string keys "
                f"{sorted(forbidden)!r} are not accepted on POST; supply "
                "all search fields in the JSON body."
            ),
        )
    payload = search_kinetics(session, body)
    return prepare_assessment_response(
        session,
        payload,
        attach_assessments=attach_kinetics_assessments,
    )
