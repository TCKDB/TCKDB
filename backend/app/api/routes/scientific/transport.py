"""Scientific transport endpoints — detail surface + search.

Three public endpoints:

- ``GET  /scientific/transport/{transport_ref_or_id}``
- ``GET  /scientific/transport/search``
- ``POST /scientific/transport/search``

The router uses a single ``/transport`` prefix; ``/search`` is
registered before ``/{handle}`` so FastAPI doesn't route the search
path through the catch-all detail handler.

See ``backend/docs/specs/scientific_transport_reads.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import (
    RecordReviewStatus,
    ScientificOriginKind,
)
from app.schemas.reads.scientific_transport import (
    ScientificTransportDetailResponse,
)
from app.schemas.reads.scientific_transport_search import (
    ScientificTransportSearchResponse,
    TransportSearchRequest,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.transport import get_transport
from app.services.scientific_read.transport_search import search_transport

router = APIRouter(prefix="/transport")


_POST_ALLOWED_QS_KEYS: set[str] = set()


@router.get("/search", response_model=ScientificTransportSearchResponse)
def scientific_transport_search_get(
    session: Session = Depends(get_db),
    species_ref: str | None = Query(None),
    species_entry_ref: str | None = Query(None),
    transport_ref: str | None = Query(None),
    model_kind: ScientificOriginKind | None = Query(None),
    has_source_calculations: bool | None = Query(None),
    has_lj_parameters: bool | None = Query(None),
    has_dipole_moment: bool | None = Query(None),
    has_polarizability: bool | None = Query(None),
    has_rotational_relaxation: bool | None = Query(None),
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
    """MVP scientific transport search.

    AND-combines the supplied filters; at least one meaningful
    filter required. Explicit ``False`` bool filter values count as
    meaningful — see
    ``backend/docs/specs/scientific_transport_reads.md``.
    """
    request_obj = TransportSearchRequest(
        species_ref=species_ref,
        species_entry_ref=species_entry_ref,
        transport_ref=transport_ref,
        model_kind=model_kind,
        has_source_calculations=has_source_calculations,
        has_lj_parameters=has_lj_parameters,
        has_dipole_moment=has_dipole_moment,
        has_polarizability=has_polarizability,
        has_rotational_relaxation=has_rotational_relaxation,
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
    payload = search_transport(session, request_obj)
    visibility = apply_internal_ids_visibility(payload)
    return _omit_unrequested_trust(visibility, payload, scope="search")


@router.post("/search", response_model=ScientificTransportSearchResponse)
def scientific_transport_search_post(
    request: Request,
    body: TransportSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/transport/search."""
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
    payload = search_transport(session, body)
    visibility = apply_internal_ids_visibility(payload)
    return _omit_unrequested_trust(visibility, payload, scope="search")


@router.get(
    "/{transport_ref_or_id}",
    response_model=ScientificTransportDetailResponse,
)
def scientific_transport_detail(
    transport_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one transport row as a scientific record.

    Path handle accepts an integer ``transport.id`` or a public ref
    of the form ``trn_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.
    """
    payload = get_transport(
        session,
        transport_handle=transport_ref_or_id,
        include=parse_include(include),
    )
    visibility = apply_internal_ids_visibility(payload)
    return _omit_unrequested_trust(visibility, payload)


def _omit_unrequested_trust(visibility, payload, *, scope: str = "detail"):
    """Drop ``record.trust`` unless the caller explicitly requested it."""
    if "trust" in set(payload.request.include):
        return visibility

    if isinstance(visibility, JSONResponse):
        import json

        data = json.loads(visibility.body)
    else:
        data = visibility.model_dump(mode="json")

    if scope == "detail":
        record = data.get("record")
        if isinstance(record, dict):
            record.pop("trust", None)
    else:
        for record in data.get("records", []) or []:
            if isinstance(record, dict):
                record.pop("trust", None)

    return JSONResponse(data)
