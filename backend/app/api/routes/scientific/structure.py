"""GET + POST /api/v1/scientific/species/structure-search.

Public chemical-structure search over species entries. The endpoint
exposes three query modes — substructure, similarity, and exact-match —
backed by the PostgreSQL RDKit cartridge.

The endpoint sits on the existing ``/species`` prefix as a sibling of
``/species/search`` so callers can discover it under the same scientific
species namespace. The route name is ``structure-search`` (not
``search``) to distinguish it from the identity/property search on
``/species/search``; the underlying matching algorithm is fundamentally
different (cartridge ``@>`` / ``tanimoto_sml`` vs. equality on
``species`` identity columns).

See ``backend/docs/specs/scientific_structure_search.md`` for the full
contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_structure_search import (
    ScientificSpeciesStructureSearchRequest,
    ScientificSpeciesStructureSearchResponse,
    StructureSearchMode,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.structure_search import (
    search_species_by_structure,
)


router = APIRouter(prefix="/species")


# POST bodies own every filter/include/pagination knob. Mirrors the
# convention enforced by the other scientific search endpoints (artifacts,
# reactions, calculations, ...).
_POST_ALLOWED_QS_KEYS: set[str] = set()


@router.get(
    "/structure-search",
    response_model=ScientificSpeciesStructureSearchResponse,
)
def species_structure_search_get(
    session: Session = Depends(get_db),
    query_smiles: str | None = Query(None),
    query_smarts: str | None = Query(None),
    query_inchi: str | None = Query(None),
    query_inchi_key: str | None = Query(None),
    mode: StructureSearchMode = Query(StructureSearchMode.substructure),
    similarity_threshold: float | None = Query(None, ge=0.0, le=1.0),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificSpeciesStructureSearchResponse:
    """RDKit-backed structure search at species-entry grain (GET form).

    Exactly one of ``query_smiles`` / ``query_smarts`` / ``query_inchi``
    / ``query_inchi_key`` must be supplied. Client-supplied ``sort=`` is
    rejected with 422 ``client_sort_not_supported``; the per-mode default
    deterministic sort always applies.
    """
    request = ScientificSpeciesStructureSearchRequest(
        query_smiles=query_smiles,
        query_smarts=query_smarts,
        query_inchi=query_inchi,
        query_inchi_key=query_inchi_key,
        mode=mode,
        similarity_threshold=similarity_threshold,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(
        search_species_by_structure(session, request)
    )


@router.post(
    "/structure-search",
    response_model=ScientificSpeciesStructureSearchResponse,
)
def species_structure_search_post(
    request: Request,
    body: ScientificSpeciesStructureSearchRequest,
    session: Session = Depends(get_db),
) -> ScientificSpeciesStructureSearchResponse:
    """RDKit-backed structure search at species-entry grain (POST form).

    All filter / include / pagination knobs live in the JSON body. Any
    query-string keys are rejected with 422
    ``post_search_fields_must_be_in_body`` (same convention as the
    other scientific search endpoints).
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
    return apply_internal_ids_visibility(
        search_species_by_structure(session, body)
    )
