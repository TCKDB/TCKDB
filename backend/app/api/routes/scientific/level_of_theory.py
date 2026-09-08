"""Scientific level-of-theory endpoints -- the methods-surface reference page.

Three public endpoints under one prefix:

- ``GET  /scientific/level-of-theories/{level_of_theory_ref_or_id}``
- ``GET  /scientific/level-of-theories/search``
- ``POST /scientific/level-of-theories/search``

The ``/search`` route is registered before ``/{handle}`` so FastAPI
doesn't route the search path through the catch-all detail handler --
same convention as ``corrections.py``.

``level_of_theory`` is reference/provenance data. It is non-reviewable
(no entry in ``SubmissionRecordType``); the response envelope still
carries an empty ``review_summary`` for shape parity with the rest of
the scientific surface.

See ``docs/plans/methods-surface.md`` §5.1-5.2 (``plan-methods-surface-v2``,
not committed to this repo) and
``backend/app/services/scientific_read/level_of_theory.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.api.routes.scientific._response import (
    DETAIL_SCOPE,
    LEVEL_OF_THEORY_RECORD_SECTIONS,
    SEARCH_SCOPE,
    omit_unrequested_sections,
)
from app.db.models.common import SpinTreatment
from app.schemas.reads.scientific_level_of_theory import (
    ScientificLevelOfTheoryDetailResponse,
)
from app.schemas.reads.scientific_level_of_theory_search import (
    LevelOfTheorySearchRequest,
    ScientificLevelOfTheorySearchResponse,
)
from app.services.scientific_read.internal_ids import apply_internal_ids_visibility
from app.services.scientific_read.level_of_theory import get_level_of_theory
from app.services.scientific_read.level_of_theory_search import (
    search_levels_of_theory,
)

router = APIRouter(prefix="/level-of-theories")

# The router-level ``?profile=`` dependency puts these two keys on every
# scientific operation, POSTs included, so they must be allowed through
# the "search fields belong in the body" guard -- same as corrections.py.
_POST_ALLOWED_QS_KEYS: set[str] = set(PROFILE_QUERY_KEYS)


@router.get("/search", response_model=ScientificLevelOfTheorySearchResponse)
def scientific_level_of_theory_search_get(
    session: Session = Depends(get_db),
    level_of_theory_ref: str | None = Query(None),
    lot_hash: str | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    dispersion: str | None = Query(None),
    solvent: str | None = Query(None),
    spin_treatment: SpinTreatment | None = Query(None),
    has_correction_schemes: bool | None = Query(None),
    has_frequency_scale_factors: bool | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    min_review_status: str | None = Query(None),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Level-of-theory search.

    AND-combines the supplied filters; at least one meaningful filter is
    required. Only levels of theory with at least one attributing
    calculation are returned -- see
    ``app/services/scientific_read/level_of_theory_search.py``.
    """
    request_obj = LevelOfTheorySearchRequest(
        level_of_theory_ref=level_of_theory_ref,
        lot_hash=lot_hash,
        method=method,
        basis=basis,
        dispersion=dispersion,
        solvent=solvent,
        spin_treatment=spin_treatment,
        has_correction_schemes=has_correction_schemes,
        has_frequency_scale_factors=has_frequency_scale_factors,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        min_review_status=min_review_status,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    payload = search_levels_of_theory(session, request_obj)
    visibility = apply_internal_ids_visibility(payload)
    return omit_unrequested_sections(
        visibility,
        payload,
        table=LEVEL_OF_THEORY_RECORD_SECTIONS,
        scope=SEARCH_SCOPE,
    )


@router.post("/search", response_model=ScientificLevelOfTheorySearchResponse)
def scientific_level_of_theory_search_post(
    request: Request,
    body: LevelOfTheorySearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/level-of-theories/search."""
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
    payload = search_levels_of_theory(session, body)
    visibility = apply_internal_ids_visibility(payload)
    return omit_unrequested_sections(
        visibility,
        payload,
        table=LEVEL_OF_THEORY_RECORD_SECTIONS,
        scope=SEARCH_SCOPE,
    )


@router.get(
    "/{level_of_theory_ref_or_id}",
    response_model=ScientificLevelOfTheoryDetailResponse,
)
def scientific_level_of_theory_detail(
    level_of_theory_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one level-of-theory row as a scientific record.

    Path handle accepts an integer ``level_of_theory.id`` or a public ref
    of the form ``lot_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.
    """
    payload = get_level_of_theory(
        session,
        level_of_theory_handle=level_of_theory_ref_or_id,
        include=parse_include(include),
    )
    visibility = apply_internal_ids_visibility(payload)
    return omit_unrequested_sections(
        visibility,
        payload,
        table=LEVEL_OF_THEORY_RECORD_SECTIONS,
        scope=DETAIL_SCOPE,
    )


__all__ = ["router"]
