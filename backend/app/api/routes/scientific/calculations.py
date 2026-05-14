"""GET /api/v1/scientific/calculations/{calculation_ref_or_id}.

Default-shape detail endpoint for retrieving one calculation as a
scientific/provenance record. Heavy include sections (results,
dependencies, parameters, constraints, artifacts, geometries,
geometry_validation, scf_stability, scan, irc, path_search) are
recognized as legal include tokens but not yet expanded — non-empty
heavy includes are rejected with 422 ``include_not_implemented_yet``.
See ``backend/docs/specs/scientific_calculation_reads.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.schemas.reads.scientific_calculation import (
    CalculationDetailRequest,
    ScientificCalculationDetailResponse,
)
from app.services.scientific_read.calculations import get_calculation
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)

router = APIRouter(prefix="/calculations")


@router.get(
    "/{calculation_ref_or_id}",
    response_model=ScientificCalculationDetailResponse,
)
def scientific_calculation_detail(
    calculation_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
) -> ScientificCalculationDetailResponse:
    """Return one calculation as a scientific/provenance record.

    Path handle accepts an integer ``calculation.id`` or a public ref
    of the form ``calc_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs and unknown ids return 404.
    Default response identifies the row by ``calculation_ref`` only —
    integer ids surface only when ``include=internal_ids`` is supplied
    *and* the deployment permits it.

    See ``docs/specs/public_identifier_policy.md`` and
    ``docs/specs/internal_ids_visibility_policy.md``.
    """
    request = CalculationDetailRequest(include=parse_include(include))
    return apply_internal_ids_visibility(
        get_calculation(
            session,
            calculation_handle=calculation_ref_or_id,
            request=request,
        )
    )
