"""GET /api/v1/scientific/geometries/{geometry_handle}.

Detail endpoint for retrieving the full coordinate payload behind a
geometry public ref. Designed as a follow-up read after
``species-calculations/search`` (which returns ``geometry_ref`` handles
but not coordinates).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_optional_current_user
from app.api.routes.scientific._common import parse_include
from app.db.models.app_user import AppUser
from app.schemas.reads.scientific_geometry import (
    GeometryReadRequest,
    ScientificGeometryResponse,
)
from app.services.scientific_read.auth_visibility import (
    apply_scientific_read_visibility,
)
from app.services.scientific_read.geometry import get_geometry

router = APIRouter(prefix="/geometries")


@router.get(
    "/{geometry_handle}",
    response_model=ScientificGeometryResponse,
)
def scientific_geometry_detail(
    geometry_handle: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
    actor: AppUser | None = Depends(get_optional_current_user),
) -> ScientificGeometryResponse:
    """Return the full coordinate payload for a geometry handle.

    Path handle accepts an integer ``geometry.id`` or a public ref of
    the form ``geom_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs return 404. Default
    response identifies the geometry by ``geometry_ref`` only —
    integer ids surface only when ``include=internal_ids`` is supplied
    and the deployment permits it.

    ``identity`` (formula / canonical SMILES / InChI key / charge /
    multiplicity of the owning species or transition-state entry) is
    always served, unauthenticated included — it names the same
    molecule the coordinates already describe, nothing more.

    ``submission_ref`` — which upload produced this geometry — is
    served only to a caller that authenticates (an ``X-API-Key``
    header or a valid session cookie). Anonymous callers do not get
    the field with a ``null`` value; the key is omitted from the
    payload entirely. See
    ``app.services.scientific_read.auth_visibility``.

    See ``docs/specs/public_identifier_policy.md`` and
    ``docs/specs/internal_ids_visibility_policy.md``.
    """
    request = GeometryReadRequest(include=parse_include(include))
    payload = get_geometry(
        session,
        geometry_handle=geometry_handle,
        request=request,
    )
    return apply_scientific_read_visibility(
        payload, authenticated=actor is not None
    )
