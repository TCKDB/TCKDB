"""GET /api/v1/scientific/species/name-search.

Finds species by a name held in ``species_name``. The query is validated and normalised before it reaches the
database and travels only as a bound parameter. See :mod:`app.services.scientific_read.species_name_search`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.server_faults import ServerFaultRoute
from app.schemas.reads.scientific_species_name_search import NameMatchMode, SpeciesNameSearchResponse
from app.services.scientific_read.internal_ids import apply_internal_ids_visibility
from app.services.scientific_read.species_name_search import search_species_names

router = APIRouter(route_class=ServerFaultRoute, prefix="/species")


@router.get(
    "/name-search",
    response_model=SpeciesNameSearchResponse,
    summary="Find species by name (exact or prefix), alphabetical and not ranked",
)
def species_name_search(
    q: str = Query(..., description="2 to 64 characters after normalisation: letters, digits, space and - , ' ( ) [ ] + . :"),
    match: NameMatchMode = Query(NameMatchMode.exact),
    limit: int = Query(10, ge=1, le=20),
    session: Session = Depends(get_db),
) -> BaseModel | JSONResponse:
    """Species whose name matches ``q`` exactly or begins with it, after normalisation.

    Names are curated by TCKDB, so a molecule with no curated name is not found here. Matches are alphabetical by
    normalised name, then by species reference; that order is not a ranking. ``truncated`` is true when more species
    match than ``limit``: refine the query. A species merged into another is listed once, as the survivor. A malformed
    query is a 422 ``species_name_query_invalid``.
    """
    return apply_internal_ids_visibility(search_species_names(session, q=q, match=match, limit=limit))
