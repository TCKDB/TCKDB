"""Scientific statmech endpoints — detail surface + search.

Three public endpoints:

- ``GET  /scientific/statmech/{statmech_ref_or_id}``
- ``GET  /scientific/statmech/search``
- ``POST /scientific/statmech/search``

The router uses a single ``/statmech`` prefix; ``/search`` is
registered before ``/{handle}`` so FastAPI doesn't swallow the search
path with the catch-all detail handler.

See ``backend/docs/specs/scientific_statmech_reads.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import omit_trust_unless_requested
from app.db.models.common import (
    RecordReviewStatus,
    StatmechTreatmentKind,
)
from app.schemas.reads.scientific_statmech import (
    ScientificStatmechDetailResponse,
)
from app.schemas.reads.scientific_statmech_search import (
    ScientificStatmechSearchResponse,
    StatmechSearchRequest,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.statmech import get_statmech
from app.services.scientific_read.statmech_search import search_statmech

router = APIRouter(prefix="/statmech")


_POST_ALLOWED_QS_KEYS: set[str] = set()


@router.get(
    "/search", response_model=ScientificStatmechSearchResponse
)
def scientific_statmech_search_get(
    session: Session = Depends(get_db),
    species_ref: str | None = Query(None),
    species_entry_ref: str | None = Query(None),
    statmech_ref: str | None = Query(None),
    conformer_group_ref: str | None = Query(None),
    conformer_observation_ref: str | None = Query(None),
    model_kind: StatmechTreatmentKind | None = Query(None),
    has_source_calculations: bool | None = Query(None),
    has_freq_calculation: bool | None = Query(None),
    has_rotor_scans: bool | None = Query(None),
    has_torsions: bool | None = Query(None),
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
    """MVP scientific statmech search.

    AND-combines the supplied filters and returns statmech records in
    the same shape as the detail endpoint. At least one meaningful
    filter is required; explicit ``False`` bool filter values count
    as meaningful — see
    ``backend/docs/specs/scientific_statmech_reads.md``.
    """
    request_obj = StatmechSearchRequest(
        species_ref=species_ref,
        species_entry_ref=species_entry_ref,
        statmech_ref=statmech_ref,
        conformer_group_ref=conformer_group_ref,
        conformer_observation_ref=conformer_observation_ref,
        model_kind=model_kind,
        has_source_calculations=has_source_calculations,
        has_freq_calculation=has_freq_calculation,
        has_rotor_scans=has_rotor_scans,
        has_torsions=has_torsions,
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
    payload = search_statmech(session, request_obj)
    visibility = apply_internal_ids_visibility(payload)
    return omit_trust_unless_requested(visibility, payload, scope="search")


@router.post(
    "/search", response_model=ScientificStatmechSearchResponse
)
def scientific_statmech_search_post(
    request: Request,
    body: StatmechSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/statmech/search.

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
    payload = search_statmech(session, body)
    visibility = apply_internal_ids_visibility(payload)
    return omit_trust_unless_requested(visibility, payload, scope="search")


@router.get(
    "/{statmech_ref_or_id}",
    response_model=ScientificStatmechDetailResponse,
)
def scientific_statmech_detail(
    statmech_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one statmech as a scientific record.

    Path handle accepts an integer ``statmech.id`` or a public ref of
    the form ``sm_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.
    """
    payload = get_statmech(
        session,
        statmech_handle=statmech_ref_or_id,
        include=parse_include(include),
    )
    visibility = apply_internal_ids_visibility(payload)
    return omit_trust_unless_requested(visibility, payload)
