"""Scientific conformer endpoints — detail surfaces + group search.

Three public endpoints:

- ``GET /scientific/conformer-groups/{conformer_group_ref_or_id}``
- ``GET /scientific/conformer-observations/{conformer_observation_ref_or_id}``
- ``GET/POST /scientific/conformers/search``

Search records are at the conformer-group grain and reuse the same
``ScientificConformerGroupRecord`` shape as the group detail endpoint.

See ``backend/docs/specs/scientific_conformer_reads.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import (
    ConformerSelectionKind,
    RecordReviewStatus,
    ScientificOriginKind,
)
from app.schemas.reads.scientific_conformer import (
    ScientificConformerGroupDetailResponse,
    ScientificConformerObservationDetailResponse,
)
from app.schemas.reads.scientific_conformer_search import (
    ConformersSearchRequest,
    ScientificConformersSearchResponse,
)
from app.services.scientific_read.conformers import (
    get_conformer_group,
    get_conformer_observation,
)
from app.services.scientific_read.conformers_search import search_conformers
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)


cg_router = APIRouter(prefix="/conformer-groups")
co_router = APIRouter(prefix="/conformer-observations")
search_router = APIRouter(prefix="/conformers")


_POST_ALLOWED_QS_KEYS: set[str] = set()


@search_router.get(
    "/search", response_model=ScientificConformersSearchResponse
)
def scientific_conformers_search_get(
    session: Session = Depends(get_db),
    species_ref: str | None = Query(None),
    species_entry_ref: str | None = Query(None),
    conformer_group_ref: str | None = Query(None),
    conformer_observation_ref: str | None = Query(None),
    selection_kind: ConformerSelectionKind | None = Query(None),
    has_selection: bool | None = Query(None),
    assignment_scheme_ref: str | None = Query(None),
    has_observations: bool | None = Query(None),
    has_calculations: bool | None = Query(None),
    has_geometries: bool | None = Query(None),
    has_opt: bool | None = Query(None),
    has_freq: bool | None = Query(None),
    has_sp: bool | None = Query(None),
    has_geometry_validation: bool | None = Query(None),
    has_scf_stability: bool | None = Query(None),
    scientific_origin: ScientificOriginKind | None = Query(None),
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
    """MVP scientific conformer-group search.

    AND-combines the supplied filters and returns conformer-group
    records in the same shape as the group detail endpoint. At least
    one meaningful filter is required; pure pagination / include /
    review knobs are not enough — see
    ``backend/docs/specs/scientific_conformer_reads.md``.
    """
    request_obj = ConformersSearchRequest(
        species_ref=species_ref,
        species_entry_ref=species_entry_ref,
        conformer_group_ref=conformer_group_ref,
        conformer_observation_ref=conformer_observation_ref,
        selection_kind=selection_kind,
        has_selection=has_selection,
        assignment_scheme_ref=assignment_scheme_ref,
        has_observations=has_observations,
        has_calculations=has_calculations,
        has_geometries=has_geometries,
        has_opt=has_opt,
        has_freq=has_freq,
        has_sp=has_sp,
        has_geometry_validation=has_geometry_validation,
        has_scf_stability=has_scf_stability,
        scientific_origin=scientific_origin,
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
        search_conformers(session, request_obj)
    )


@search_router.post(
    "/search", response_model=ScientificConformersSearchResponse
)
def scientific_conformers_search_post(
    request: Request,
    body: ConformersSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/conformers/search.

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
    return apply_internal_ids_visibility(search_conformers(session, body))


@cg_router.get(
    "/{conformer_group_ref_or_id}",
    response_model=ScientificConformerGroupDetailResponse,
)
def scientific_conformer_group_detail(
    conformer_group_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one conformer group as a scientific record.

    Path handle accepts an integer ``conformer_group.id`` or a public
    ref of the form ``cg_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.
    """
    return apply_internal_ids_visibility(
        get_conformer_group(
            session,
            conformer_group_handle=conformer_group_ref_or_id,
            include=parse_include(include),
        )
    )


@co_router.get(
    "/{conformer_observation_ref_or_id}",
    response_model=ScientificConformerObservationDetailResponse,
)
def scientific_conformer_observation_detail(
    conformer_observation_ref_or_id: str = Path(
        ..., min_length=1, max_length=64
    ),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one conformer observation as a scientific record.

    Path handle accepts an integer ``conformer_observation.id`` or a
    public ref of the form ``co_…``. Same 422 / 404 contract as the
    group surface.
    """
    return apply_internal_ids_visibility(
        get_conformer_observation(
            session,
            conformer_observation_handle=conformer_observation_ref_or_id,
            include=parse_include(include),
        )
    )
