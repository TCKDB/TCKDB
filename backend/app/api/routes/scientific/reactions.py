"""GET + POST /api/v1/scientific/reactions/search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_reactions import (
    ReactionDirectionQuery,
    ReactionMatchMode,
    ReactionSearchRequest,
    ScientificReactionSearchResponse,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.reactions import search_reactions

router = APIRouter(prefix="/reactions")


@router.get("/search", response_model=ScientificReactionSearchResponse)
def reaction_search_get(
    session: Session = Depends(get_db),
    reactants: list[str] | None = Query(None),
    products: list[str] | None = Query(None),
    direction: ReactionDirectionQuery = Query(ReactionDirectionQuery.either),
    match: ReactionMatchMode = Query(ReactionMatchMode.contains),
    family: str | None = Query(None),
    reaction_ref: str | None = Query(None),
    reaction_entry_ref: str | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    collapse: CollapseMode = Query(CollapseMode.all),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificReactionSearchResponse:
    """Discover reaction entries by reactants/products with availability + trust.

    Repeated ``reactants=`` and ``products=`` are accepted and preserve list
    order. ``direction=exact`` is rejected (v0). ``sort=`` is rejected (v0).

    ``match=contains`` (the default) means set containment per role, so
    ``?reactants=NN`` returns every reaction with NN among its reactants and
    leaves the product side unconstrained. ``match=exact`` asks for precisely
    one equation, both sides, counts included. See
    ``docs/specs/read_api_mvp.md`` §Endpoint 2.
    """
    request = ReactionSearchRequest(
        reactants=reactants or [],
        products=products or [],
        direction=direction,
        match=match,
        family=family,
        reaction_ref=reaction_ref,
        reaction_entry_ref=reaction_entry_ref,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        collapse=collapse,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(search_reactions(session, request))


# Allowed query-string keys on the POST endpoint. Any other key produces
# 422 ``post_search_fields_must_be_in_body``.
# The router-level ``?profile=`` dependency puts these two keys on every
# scientific operation, POSTs included, so they must be allowed through the
# "search fields belong in the body" guard. Everything else is still
# rejected rather than silently ignored.
_POST_ALLOWED_QS_KEYS: set[str] = set(PROFILE_QUERY_KEYS)


@router.post("/search", response_model=ScientificReactionSearchResponse)
def reaction_search_post(
    request: Request,
    body: ReactionSearchRequest,
    session: Session = Depends(get_db),
) -> ScientificReactionSearchResponse:
    """JSON-body variant of reaction search for complex reactant/product lists.

    All search fields, filters, includes, collapse, offset, and limit live in
    the body. Query-string parameters are rejected (per Phase 2.1 patch),
    except for any infrastructure-level params that may be added later. Body
    field ``sort`` is rejected by the service layer (v0 sort policy).
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
    return apply_internal_ids_visibility(search_reactions(session, body))
