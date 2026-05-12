"""GET + POST /api/v1/scientific/thermo/search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_thermo import ThermoModelKindQuery
from app.schemas.reads.scientific_thermo_search import (
    ScientificThermoSearchResponse,
    ThermoSearchRequest,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.thermo_search import search_thermo

router = APIRouter(prefix="/thermo")

_POST_ALLOWED_QS_KEYS: set[str] = set()


@router.get("/search", response_model=ScientificThermoSearchResponse)
def thermo_search_get(
    session: Session = Depends(get_db),
    smiles: str | None = Query(None),
    inchi: str | None = Query(None),
    inchi_key: str | None = Query(None),
    formula: str | None = Query(None),
    charge: int | None = Query(None),
    multiplicity: int | None = Query(None),
    electronic_state_kind: SpeciesEntryStateKind | None = Query(None),
    species_entry_kind: StationaryPointKind | None = Query(None),
    species_ref: str | None = Query(None),
    species_entry_ref: str | None = Query(None),
    temperature_min: float | None = Query(None),
    temperature_max: float | None = Query(None),
    model_kind: ThermoModelKindQuery | None = Query(None),
    level_of_theory_id: int | None = Query(None),
    level_of_theory_ref: str | None = Query(None),
    software: str | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    collapse: CollapseMode = Query(CollapseMode.all),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificThermoSearchResponse:
    """Chemistry-first thermo search by species identifiers.

    At least one of ``smiles`` / ``inchi`` / ``inchi_key`` / ``formula``
    must be supplied; multiple identifiers AND-combine. Returns thermo
    records with the resolved species/species_entry identity attached, so
    workflow tools never need to know the entry id up front.
    """
    request = ThermoSearchRequest(
        smiles=smiles,
        inchi=inchi,
        inchi_key=inchi_key,
        formula=formula,
        charge=charge,
        multiplicity=multiplicity,
        electronic_state_kind=electronic_state_kind,
        species_entry_kind=species_entry_kind,
        species_ref=species_ref,
        species_entry_ref=species_entry_ref,
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        model_kind=model_kind,
        level_of_theory_id=level_of_theory_id,
        level_of_theory_ref=level_of_theory_ref,
        software=software,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        collapse=collapse,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(search_thermo(session, request))


@router.post("/search", response_model=ScientificThermoSearchResponse)
def thermo_search_post(
    request: Request,
    body: ThermoSearchRequest,
    session: Session = Depends(get_db),
) -> ScientificThermoSearchResponse:
    """JSON-body variant for structured thermo search.

    All search fields, filters, includes, collapse, offset, and limit live
    in the body. Query-string parameters are rejected (per Phase 4 POST
    convention). ``sort`` in the body is rejected by the service layer.
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
    return apply_internal_ids_visibility(search_thermo(session, body))
