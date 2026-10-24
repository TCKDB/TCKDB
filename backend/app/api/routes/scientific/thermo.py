"""GET /api/v1/scientific/species-entries/{species_entry_id}/thermo.

Phase C: the path parameter now accepts either the integer
``species_entry.id`` or a public ref of the form ``spe_...``. The URL
template keeps the historical ``{species_entry_id}`` name for backwards
compatibility with OpenAPI consumers. See
``docs/specs/public_identifier_policy.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import (
    omit_trust_unless_requested,
    prepare_assessment_response,
    require_bounded_input_check,
)
from app.db.models.common import RecordReviewStatus
from app.schemas.reads.removed_parameters import THERMO as _REMOVED_PARAMETERS
from app.schemas.reads.removed_parameters import reject_removed_query_params
from app.schemas.reads.scientific_thermo import (
    ScientificSpeciesThermoResponse,
    ThermoModelKindQuery,
    ThermoReadRequest,
)
from app.services.scientific_read.handles import resolve_species_entry_handle
from app.services.scientific_read.public_assessments import (
    attach_thermo_assessments,
)
from app.services.scientific_read.thermo import get_species_thermo

router = APIRouter(prefix="/species-entries")


@router.get(
    "/{species_entry_id}/thermo",
    response_model=ScientificSpeciesThermoResponse,
)
def species_thermo(
    http_request: Request,
    species_entry_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    coverage_temperature_min_k: float | None = Query(None),
    coverage_temperature_max_k: float | None = Query(None),
    model_kind: ThermoModelKindQuery | None = Query(None),
    level_of_theory_id: int | None = Query(None),
    level_of_theory_ref: str | None = Query(None),
    software: str | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    check_inputs: bool = Query(
        False,
        description="With include=assessments: compare each stored assessment's inputs with today's. Opt-in, at most 10 records.",
    ),
) -> ScientificSpeciesThermoResponse:
    """Return every thermo record for a species entry, in display order.

    Review status, then newest deposition, then id (``docs/specs/browse_order.md``). The read never picks one:
    ``collapse`` and ``selection_policy`` are refused (422); ``POST .../thermo/select`` answers "give me one".
    ``coverage_temperature_min_k`` / ``coverage_temperature_max_k`` filter nothing; they set the window each record's
    ``temperature_coverage`` is computed against.

    Path handle is strictly the species-entry resource: an integer
    ``species_entry.id`` or a public ref starting with ``spe_``. A
    ``species.id`` (or any other prefix) returns 422 / 404.
    ``sort=`` is rejected (v0). See ``docs/specs/browse_order.md``
    §Endpoint 4 and ``docs/specs/public_identifier_policy.md``.
    """
    reject_removed_query_params(http_request, _REMOVED_PARAMETERS)
    resolved_species_entry_id = resolve_species_entry_handle(session, species_entry_id)
    request = ThermoReadRequest(
        coverage_temperature_min_k=coverage_temperature_min_k,
        coverage_temperature_max_k=coverage_temperature_max_k,
        model_kind=model_kind,
        level_of_theory_id=level_of_theory_id,
        level_of_theory_ref=level_of_theory_ref,
        software=software,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
        check_inputs=check_inputs,
    )
    require_bounded_input_check(request_include=request.include, check_inputs=check_inputs, limit=limit)
    payload = get_species_thermo(
        session,
        species_entry_id=resolved_species_entry_id,
        request=request,
    )
    visibility = prepare_assessment_response(
        session,
        payload,
        attach_assessments=attach_thermo_assessments,
        check_inputs=check_inputs,
    )
    return omit_trust_unless_requested(visibility, payload, scope="search")
