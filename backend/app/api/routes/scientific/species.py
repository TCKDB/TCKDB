"""GET /api/v1/scientific/species/search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_species import (
    ScientificSpeciesSearchResponse,
    SpeciesSearchRequest,
)
from app.services.scientific_read.species import search_species

router = APIRouter(prefix="/species")


@router.get("/search", response_model=ScientificSpeciesSearchResponse)
def species_search(
    session: Session = Depends(get_db),
    smiles: str | None = Query(None),
    inchi: str | None = Query(None),
    inchi_key: str | None = Query(None),
    formula: str | None = Query(None),
    charge: int | None = Query(None),
    multiplicity: int | None = Query(None),
    electronic_state_kind: SpeciesEntryStateKind | None = Query(None),
    species_entry_kind: StationaryPointKind | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    collapse: CollapseMode = Query(CollapseMode.all),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificSpeciesSearchResponse:
    """Discover species by chemical identity with per-entry trust + availability.

    Multiple identifiers AND-combine; inconsistent identifiers return an
    empty result set rather than 422. Client-supplied ``sort=`` is rejected
    with 422 (``client_sort_not_supported``). See ``docs/specs/read_api_mvp.md``
    §Endpoint 1 for the contract.
    """
    request = SpeciesSearchRequest(
        smiles=smiles,
        inchi=inchi,
        inchi_key=inchi_key,
        formula=formula,
        charge=charge,
        multiplicity=multiplicity,
        electronic_state_kind=electronic_state_kind,
        species_entry_kind=species_entry_kind,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        collapse=collapse,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return search_species(session, request)
