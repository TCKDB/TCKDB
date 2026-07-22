"""GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics.

Phase C: the path parameter now accepts either the integer
``reaction_entry.id`` or a public ref of the form ``rxe_...``. The URL
template keeps the historical ``{reaction_entry_id}`` name for backwards
compatibility with OpenAPI consumers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import (
    omit_trust_unless_requested,
    prepare_assessment_response,
)
from app.db.models.common import KineticsModelKind, RecordReviewStatus
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_kinetics import (
    KineticsReadRequest,
    ScientificReactionKineticsResponse,
)
from app.services.scientific_read.handles import resolve_reaction_entry_handle
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.services.scientific_read.public_assessments import (
    attach_kinetics_assessments,
)

router = APIRouter(prefix="/reaction-entries")


@router.get(
    "/{reaction_entry_id}/kinetics",
    response_model=ScientificReactionKineticsResponse,
)
def reaction_kinetics(
    reaction_entry_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    temperature_min: float | None = Query(None),
    temperature_max: float | None = Query(None),
    pressure_bar: float | None = Query(
        None,
        gt=0,
        allow_inf_nan=False,
        description="Requested pressure in bar.",
    ),
    pressure: float | None = Query(
        None,
        gt=0,
        allow_inf_nan=False,
        deprecated=True,
        description="Deprecated alias for pressure_bar; retained for one release.",
    ),
    model_kind: KineticsModelKind | None = Query(None),
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
) -> ScientificReactionKineticsResponse:
    """Return kinetics records for a reaction entry, sorted per D9.

    Path handle is strictly the reaction-entry resource: an integer
    ``reaction_entry.id`` or a public ref starting with ``rxe_``. A
    ``chem_reaction.id`` (or any other prefix) returns 422 / 404.
    Provenance keys are always present; TS-chain fields are populated
    only for TS-backed records (Phase 2.2). Default sort is the locked
    D9 chain. ``sort=`` is rejected (v0). See ``docs/specs/read_api_mvp.md``
    §Endpoint 3 and ``docs/specs/public_identifier_policy.md``.
    """
    # Validate the request (pressure alias conflict, etc.) before resolving
    # the resource handle, so a malformed request is rejected with 422
    # regardless of whether the reaction entry exists (request validation
    # precedes resource lookup).
    request = KineticsReadRequest(
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        pressure_bar=pressure_bar,
        pressure=pressure,
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
    resolved_reaction_entry_id = resolve_reaction_entry_handle(session, reaction_entry_id)
    payload = get_reaction_kinetics(
        session,
        reaction_entry_id=resolved_reaction_entry_id,
        request=request,
    )
    visibility = prepare_assessment_response(
        session,
        payload,
        attach_assessments=attach_kinetics_assessments,
    )
    return omit_trust_unless_requested(visibility, payload, scope="search")
