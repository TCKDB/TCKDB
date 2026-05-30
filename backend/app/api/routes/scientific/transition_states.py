"""Scientific transition-state read/search endpoints.

Two detail surfaces and one search surface:

- ``GET /scientific/transition-states/{transition_state_ref_or_id}``
- ``GET /scientific/transition-state-entries/{transition_state_entry_ref_or_id}``
- ``GET/POST /scientific/transition-states/search``

The search surface returns records at the TS-entry grain. See
``backend/docs/specs/scientific_transition_state_reads.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import omit_trust_unless_requested
from app.db.models.common import (
    RecordReviewStatus,
    TransitionStateEntryStatus,
)
from app.schemas.reads.scientific_transition_state import (
    ScientificTransitionStateDetailResponse,
    ScientificTransitionStateEntryDetailResponse,
    TransitionStateDetailRequest,
    TransitionStateEntryDetailRequest,
)
from app.schemas.reads.scientific_transition_state_search import (
    ScientificTransitionStatesSearchResponse,
    TransitionStatesSearchRequest,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.transition_states import (
    get_transition_state,
    get_transition_state_entry,
)
from app.services.scientific_read.transition_states_search import (
    search_transition_states,
)


# Two prefixes, two routers, one module.
ts_router = APIRouter(prefix="/transition-states")
tse_router = APIRouter(prefix="/transition-state-entries")


_POST_ALLOWED_QS_KEYS: set[str] = set()


# ---------------------------------------------------------------------------
# Search (registered before the catch-all detail handler so /search routes
# don't get swallowed by ``/{handle}``)
# ---------------------------------------------------------------------------


@ts_router.get(
    "/search", response_model=ScientificTransitionStatesSearchResponse
)
def scientific_transition_states_search_get(
    session: Session = Depends(get_db),
    reaction_ref: str | None = Query(None),
    reaction_entry_ref: str | None = Query(None),
    transition_state_ref: str | None = Query(None),
    transition_state_entry_ref: str | None = Query(None),
    status: TransitionStateEntryStatus | None = Query(None),
    charge: int | None = Query(None),
    multiplicity: int | None = Query(None, ge=1),
    has_calculations: bool | None = Query(None),
    has_opt: bool | None = Query(None),
    has_freq: bool | None = Query(None),
    has_sp: bool | None = Query(None),
    has_irc: bool | None = Query(None),
    has_path_search: bool | None = Query(None),
    has_geometry_validation: bool | None = Query(None),
    has_scf_stability: bool | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    software: str | None = Query(None),
    software_version: str | None = Query(None),
    workflow_tool: str | None = Query(None),
    workflow_tool_version: str | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """MVP scientific transition-state search.

    AND-combines the supplied filters and returns
    transition-state-entry records in the same shape as the TS-entry
    detail endpoint. At least one meaningful filter is required; pure
    pagination / include / review knobs are not enough — see
    ``backend/docs/specs/scientific_transition_state_reads.md``.
    """
    request_obj = TransitionStatesSearchRequest(
        reaction_ref=reaction_ref,
        reaction_entry_ref=reaction_entry_ref,
        transition_state_ref=transition_state_ref,
        transition_state_entry_ref=transition_state_entry_ref,
        status=status,
        charge=charge,
        multiplicity=multiplicity,
        has_calculations=has_calculations,
        has_opt=has_opt,
        has_freq=has_freq,
        has_sp=has_sp,
        has_irc=has_irc,
        has_path_search=has_path_search,
        has_geometry_validation=has_geometry_validation,
        has_scf_stability=has_scf_stability,
        method=method,
        basis=basis,
        software=software,
        software_version=software_version,
        workflow_tool=workflow_tool,
        workflow_tool_version=workflow_tool_version,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(
        search_transition_states(session, request_obj)
    )


@ts_router.post(
    "/search", response_model=ScientificTransitionStatesSearchResponse
)
def scientific_transition_states_search_post(
    request: Request,
    body: TransitionStatesSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/transition-states/search.

    All filters live in the body. Query-string parameters are rejected
    with 422 ``post_search_fields_must_be_in_body``.
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
    return apply_internal_ids_visibility(search_transition_states(session, body))


# ---------------------------------------------------------------------------
# Detail endpoints
# ---------------------------------------------------------------------------


@ts_router.get(
    "/{transition_state_ref_or_id}",
    response_model=ScientificTransitionStateDetailResponse,
)
def scientific_transition_state_detail(
    transition_state_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one transition-state concept as a scientific record.

    Path handle accepts an integer ``transition_state.id`` or a public
    ref of the form ``ts_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs and unknown ids return 404.
    Default response identifies the row by ``transition_state_ref``
    only — integer ids surface only when ``include=internal_ids`` is
    supplied *and* the deployment permits it.
    """
    req = TransitionStateDetailRequest(include=parse_include(include))
    return apply_internal_ids_visibility(
        get_transition_state(
            session,
            transition_state_handle=transition_state_ref_or_id,
            request=req,
        )
    )


@tse_router.get(
    "/{transition_state_entry_ref_or_id}",
    response_model=ScientificTransitionStateEntryDetailResponse,
)
def scientific_transition_state_entry_detail(
    transition_state_entry_ref_or_id: str = Path(
        ..., min_length=1, max_length=64
    ),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one transition-state-entry as a scientific record.

    Path handle accepts an integer ``transition_state_entry.id`` or a
    public ref of the form ``tse_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs and unknown ids return 404.

    Deterministic trust / evidence metadata
    (``computed_transition_state_v1``) is attached to the record only
    when ``include=trust`` is supplied explicitly — ``include=all`` does
    not pull it in, and the default response omits the field entirely so
    it stays byte-identical to its pre-trust shape.
    """
    req = TransitionStateEntryDetailRequest(include=parse_include(include))
    payload = get_transition_state_entry(
        session,
        transition_state_entry_handle=transition_state_entry_ref_or_id,
        request=req,
    )
    visibility = apply_internal_ids_visibility(payload)
    return omit_trust_unless_requested(visibility, payload)
