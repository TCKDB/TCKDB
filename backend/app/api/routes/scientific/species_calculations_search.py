"""GET + POST /api/v1/scientific/species-calculations/search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
    ScientificOriginKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_species_calculations import (
    CalculationRanking,
    ScientificSpeciesCalculationsSearchResponse,
    SpeciesCalculationsSearchRequest,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.species_calculations_search import (
    search_species_calculations,
)

router = APIRouter(prefix="/species-calculations")

_POST_ALLOWED_QS_KEYS: set[str] = set()


@router.get(
    "/search", response_model=ScientificSpeciesCalculationsSearchResponse
)
def species_calculations_search_get(
    session: Session = Depends(get_db),
    smiles: str | None = Query(None),
    inchi: str | None = Query(None),
    inchi_key: str | None = Query(None),
    formula: str | None = Query(None),
    charge: int | None = Query(None),
    multiplicity: int | None = Query(None),
    electronic_state_kind: SpeciesEntryStateKind | None = Query(None),
    species_entry_kind: StationaryPointKind | None = Query(None),
    species_id: int | None = Query(None, ge=1),
    species_entry_id: int | None = Query(None, ge=1),
    species_ref: str | None = Query(None),
    species_entry_ref: str | None = Query(None),
    calculation_type: CalculationType | None = Query(None),
    level_of_theory_id: int | None = Query(None),
    level_of_theory_ref: str | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    software: str | None = Query(None),
    workflow_tool: str | None = Query(None),
    scientific_origin: ScientificOriginKind | None = Query(None),
    calculation_quality: CalculationQuality | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    include_rejected_quality: bool = Query(False),
    ranking: CalculationRanking = Query(CalculationRanking.default),
    sort: str | None = Query(None),
    collapse: CollapseMode = Query(CollapseMode.all),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificSpeciesCalculationsSearchResponse:
    """Chemistry-first species calculation/conformer search.

    Calculation-centered records that include resolved species identity,
    energy (when applicable), level of theory, software, conformer
    context (when present), geometry IDs, validation, review state, and
    provenance. See ``docs/specs/species_calculation_search_api.md``.
    """
    request = SpeciesCalculationsSearchRequest(
        smiles=smiles,
        inchi=inchi,
        inchi_key=inchi_key,
        formula=formula,
        charge=charge,
        multiplicity=multiplicity,
        electronic_state_kind=electronic_state_kind,
        species_entry_kind=species_entry_kind,
        species_id=species_id,
        species_entry_id=species_entry_id,
        species_ref=species_ref,
        species_entry_ref=species_entry_ref,
        calculation_type=calculation_type,
        level_of_theory_id=level_of_theory_id,
        level_of_theory_ref=level_of_theory_ref,
        method=method,
        basis=basis,
        software=software,
        workflow_tool=workflow_tool,
        scientific_origin=scientific_origin,
        calculation_quality=calculation_quality,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        include_rejected_quality=include_rejected_quality,
        ranking=ranking,
        sort=sort,
        collapse=collapse,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(
        search_species_calculations(session, request)
    )


@router.post(
    "/search", response_model=ScientificSpeciesCalculationsSearchResponse
)
def species_calculations_search_post(
    request: Request,
    body: SpeciesCalculationsSearchRequest,
    session: Session = Depends(get_db),
) -> ScientificSpeciesCalculationsSearchResponse:
    """JSON-body variant for structured species-calculation queries.

    All search fields, filters, includes, ranking, collapse, offset, and
    limit live in the body. Query-string parameters are rejected
    (Phase 4 / Phase 6 POST convention). Body field ``sort`` is rejected
    by the service layer (v0 sort policy).
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
        search_species_calculations(session, body)
    )
