"""GET + POST /api/v1/scientific/kinetics/search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.api.routes.scientific._response import (
    ANYWHERE_SCOPE,
    omit_trust_unless_requested,
    prepare_assessment_response,
)
from app.db.models.common import KineticsModelKind, RecordReviewStatus
from app.schemas.reads.removed_parameters import KINETICS as _REMOVED_PARAMETERS
from app.schemas.reads.removed_parameters import reject_removed_query_params
from app.schemas.reads.scientific_kinetics_search import (
    KineticsSearchRequest,
    ScientificKineticsSearchResponse,
)
from app.schemas.reads.scientific_reactions import (
    ReactionDirectionQuery,
    ReactionMatchMode,
)
from app.services.scientific_read.kinetics_search import search_kinetics
from app.services.scientific_read.public_assessments import (
    attach_kinetics_assessments,
)

router = APIRouter(prefix="/kinetics")

# The router-level ``?profile=`` dependency puts these two keys on every
# scientific operation, POSTs included, so they must be allowed through the
# "search fields belong in the body" guard. Everything else is still
# rejected rather than silently ignored.
_POST_ALLOWED_QS_KEYS: set[str] = set(PROFILE_QUERY_KEYS)


@router.get("/search", response_model=ScientificKineticsSearchResponse)
def kinetics_search_get(
    http_request: Request,
    session: Session = Depends(get_db),
    reactants: list[str] | None = Query(None),
    products: list[str] | None = Query(None),
    direction: ReactionDirectionQuery = Query(ReactionDirectionQuery.either),
    match: ReactionMatchMode = Query(ReactionMatchMode.contains),
    family: str | None = Query(None),
    reaction_ref: str | None = Query(None),
    reaction_entry_ref: str | None = Query(None),
    coverage_temperature_min_k: float | None = Query(None),
    coverage_temperature_max_k: float | None = Query(None),
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
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificKineticsSearchResponse:
    """Chemistry-first kinetics search by reactants/products.

    Repeated ``reactants=`` and ``products=`` are accepted on the GET form.
    ``match=contains`` (the default) means every queried species must appear
    in that role and an omitted side constrains nothing; ``match=exact``
    demands the whole equation. For complex reactant/product lists
    (especially with bracket-heavy SMILES), prefer the POST form. Returns
    kinetics records with the resolved reaction/reaction_entry identity
    attached. Every matching record is returned, ordered by the record's own
    review status, then newest deposition, then id. ``collapse`` is refused
    (422); ``POST /reaction-entries/{ref}/kinetics/select`` answers
    "give me one".
    """
    reject_removed_query_params(http_request, _REMOVED_PARAMETERS)
    request = KineticsSearchRequest(
        reactants=reactants or [],
        products=products or [],
        direction=direction,
        match=match,
        family=family,
        reaction_ref=reaction_ref,
        reaction_entry_ref=reaction_entry_ref,
        coverage_temperature_min_k=coverage_temperature_min_k,
        coverage_temperature_max_k=coverage_temperature_max_k,
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
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    payload = search_kinetics(session, request)
    visibility = prepare_assessment_response(
        session,
        payload,
        attach_assessments=attach_kinetics_assessments,
    )
    # ``ANYWHERE_SCOPE``, not ``SEARCH_SCOPE``: a record's top level here is
    # exactly ``['kinetics', 'reaction']`` and ``trust`` sits inside the
    # ``kinetics`` wrapper. See the longer note on the thermo twin.
    return omit_trust_unless_requested(visibility, payload, scope=ANYWHERE_SCOPE)


@router.post("/search", response_model=ScientificKineticsSearchResponse)
def kinetics_search_post(
    request: Request,
    body: KineticsSearchRequest,
    session: Session = Depends(get_db),
) -> ScientificKineticsSearchResponse:
    """JSON-body variant for structured reactant/product queries.

    All search fields, filters, includes, offset, and limit live
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
    visibility = prepare_assessment_response(
        session,
        payload,
        attach_assessments=attach_kinetics_assessments,
    )
    return omit_trust_unless_requested(visibility, payload, scope=ANYWHERE_SCOPE)
