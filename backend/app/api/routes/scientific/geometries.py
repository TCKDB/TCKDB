"""GET /api/v1/scientific/geometries/{geometry_handle}.

Detail endpoint for retrieving the full coordinate payload behind a
geometry public ref. Designed as a follow-up read after
``species-calculations/search`` (which returns ``geometry_ref`` handles
but not coordinates).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.schemas.reads.scientific_geometry import (
    GeometryReadRequest,
    ScientificGeometryResponse,
)
from app.services.scientific_read.geometry import get_geometry
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)

router = APIRouter(prefix="/geometries")


@router.get(
    "/{geometry_handle}",
    response_model=ScientificGeometryResponse,
)
def scientific_geometry_detail(
    geometry_handle: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
) -> ScientificGeometryResponse:
    """Return the full coordinate payload for a geometry handle.

    Path handle accepts an integer ``geometry.id`` or a public ref of
    the form ``geom_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs return 404. Default
    response identifies the geometry by ``geometry_ref`` only —
    integer ids surface only when ``include=internal_ids`` is supplied
    and the deployment permits it.

    See ``docs/specs/public_identifier_policy.md`` and
    ``docs/specs/internal_ids_visibility_policy.md``.
    """
    request = GeometryReadRequest(include=parse_include(include))
    return apply_internal_ids_visibility(
        get_geometry(
            session,
            geometry_handle=geometry_handle,
            request=request,
        )
    )
