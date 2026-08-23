"""GET + POST /api/v1/scientific/thermo/search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.api.routes.scientific._response import (
    ANYWHERE_SCOPE,
    omit_trust_unless_requested,
    prepare_assessment_response,
)
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
from app.services.scientific_read.public_assessments import (
    attach_thermo_assessments,
)
from app.services.scientific_read.thermo_search import search_thermo

router = APIRouter(prefix="/thermo")

# The router-level ``?profile=`` dependency puts these two keys on every
# scientific operation, POSTs included, so they must be allowed through the
# "search fields belong in the body" guard. Everything else is still
# rejected rather than silently ignored.
_POST_ALLOWED_QS_KEYS: set[str] = set(PROFILE_QUERY_KEYS)


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
    payload = search_thermo(session, request)
    visibility = prepare_assessment_response(
        session,
        payload,
        attach_assessments=attach_thermo_assessments,
    )
    # ``ANYWHERE_SCOPE``, not ``SEARCH_SCOPE``. A record's top level here is
    # exactly ``['species', 'thermo']``; ``trust`` sits inside the ``thermo``
    # wrapper one level down, so a search-scoped strip would iterate the
    # records, pop nothing, and leave every assertion that "no error
    # occurred" passing. The same asymmetry is why ``assessments`` — which
    # hard-codes the payload-wide scope — has always behaved correctly at
    # this exact depth on this exact response.
    return omit_trust_unless_requested(visibility, payload, scope=ANYWHERE_SCOPE)


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
    payload = search_thermo(session, body)
    visibility = prepare_assessment_response(
        session,
        payload,
        attach_assessments=attach_thermo_assessments,
    )
    return omit_trust_unless_requested(visibility, payload, scope=ANYWHERE_SCOPE)
