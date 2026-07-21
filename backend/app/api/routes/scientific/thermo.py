"""GET /api/v1/scientific/species-entries/{species_entry_id}/thermo.

Phase C: the path parameter now accepts either the integer
``species_entry.id`` or a public ref of the form ``spe_...``. The URL
template keeps the historical ``{species_entry_id}`` name for backwards
compatibility with OpenAPI consumers. See
``docs/specs/public_identifier_policy.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import (
    omit_assessments_unless_requested,
    omit_trust_unless_requested,
)
from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import CollapseMode, SelectionPolicy
from app.schemas.reads.scientific_thermo import (
    ScientificSpeciesThermoResponse,
    ThermoModelKindQuery,
    ThermoReadRequest,
)
from app.services.scientific_read.handles import resolve_species_entry_handle
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
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
    species_entry_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
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
    selection_policy: SelectionPolicy = Query(SelectionPolicy.default),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificSpeciesThermoResponse:
    """Return thermo records for a species entry, sorted per L3.

    Path handle is strictly the species-entry resource: an integer
    ``species_entry.id`` or a public ref starting with ``spe_``. A
    ``species.id`` (or any other prefix) returns 422 / 404.
    ``sort=`` is rejected (v0). See ``docs/specs/read_api_mvp.md``
    §Endpoint 4 and ``docs/specs/public_identifier_policy.md``.
    """
    resolved_species_entry_id = resolve_species_entry_handle(session, species_entry_id)
    request = ThermoReadRequest(
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
        selection_policy=selection_policy,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    payload = get_species_thermo(
        session,
        species_entry_id=resolved_species_entry_id,
        request=request,
    )
    if "assessments" in set(payload.request.include):
        attach_thermo_assessments(session, payload)
    visibility = apply_internal_ids_visibility(payload)
    visibility = omit_assessments_unless_requested(visibility, payload)
    return omit_trust_unless_requested(visibility, payload, scope="search")
