"""Specialized full-data scientific calculation path endpoints.

Companion routes to ``calculations.py``: the detail/search surface
returns bounded summary blocks under the heavy include tokens
``scan`` / ``irc`` / ``path_search``; this module exposes the full
per-point trajectory data behind paginated, abuse-bound URLs:

- ``GET /scientific/calculations/{calculation_ref_or_id}/scan``
- ``GET /scientific/calculations/{calculation_ref_or_id}/irc``
- ``GET /scientific/calculations/{calculation_ref_or_id}/path-search``

See ``backend/docs/specs/scientific_calculation_path_includes.md``.

Mounted under the same ``/calculations`` prefix as the detail router
but registered earlier in
``app/api/routes/scientific/__init__.py`` so OpenAPI lists the
path-data endpoints alongside the detail one. Path collisions are
not an issue because ``/{handle}/scan`` is a deeper path segment
than ``/{handle}`` and FastAPI matches on path structure, not
include order.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.schemas.reads.scientific_calculation_paths import (
    ScientificCalculationIRCResponse,
    ScientificCalculationPathSearchResponse,
    ScientificCalculationScanResponse,
)
from app.services.scientific_read.calculation_paths import (
    get_calculation_irc,
    get_calculation_path_search,
    get_calculation_scan,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)

router = APIRouter(prefix="/calculations")


@router.get(
    "/{calculation_ref_or_id}/scan",
    response_model=ScientificCalculationScanResponse,
)
def scientific_calculation_scan(
    calculation_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include_geometries: bool = Query(False),
    include: list[str] | None = Query(None),
    sort: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Return full scan data for one calculation, paginated by point.

    Path handle accepts an integer ``calculation.id`` or a public ref
    of the form ``calc_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs and unknown ids return 404.
    A calculation with no ``calc_scan_result`` row returns 404
    ``scan_result_not_found`` (matches the legacy
    ``/api/v1/calculations/{id}/scan-result`` semantics — the
    requested resource genuinely doesn't exist for this calc).

    Pagination applies to the ``points`` array only — coordinates are
    returned in full because their cardinality is bounded by
    ``calc_scan_result.dimension``. Per-point geometries appear as
    ``geometry_ref`` only by default; ``include_geometries=true`` adds
    a lightweight ``geometry_link`` block (ref + natoms + geom_hash).
    Full coordinate payloads remain accessible only via
    ``GET /scientific/geometries/{geometry_ref}``.

    Internal-ID visibility follows the existing Phase D policy.
    Client-supplied ``sort=`` is rejected with 422
    ``client_sort_not_supported``.
    """
    payload = get_calculation_scan(
        session,
        calculation_handle=calculation_ref_or_id,
        include_geometries=include_geometries,
        include=parse_include(include),
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(payload)


@router.get(
    "/{calculation_ref_or_id}/irc",
    response_model=ScientificCalculationIRCResponse,
)
def scientific_calculation_irc(
    calculation_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include_geometries: bool = Query(False),
    include: list[str] | None = Query(None),
    sort: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Return full IRC data for one calculation, paginated by point.

    Same handle / pagination / sort / include / 404 contract as
    ``/scan`` (see that route's docstring for the full contract).
    Calcs with no ``calc_irc_result`` row return 404
    ``irc_result_not_found``.

    Pagination applies to the ``points`` array only; the per-row
    direction/is_ts/reaction-coordinate state and gradient norms come
    through verbatim. Per-point geometries appear as ``geometry_ref``
    only by default; ``include_geometries=true`` adds the lightweight
    ``geometry_link`` block (ref + natoms + geom_hash). Full
    coordinate payloads remain accessible only via
    ``GET /scientific/geometries/{geometry_ref}``.

    Internal-ID visibility follows the existing Phase D policy via
    ``include=internal_ids``.
    """
    payload = get_calculation_irc(
        session,
        calculation_handle=calculation_ref_or_id,
        include_geometries=include_geometries,
        include=parse_include(include),
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(payload)


@router.get(
    "/{calculation_ref_or_id}/path-search",
    response_model=ScientificCalculationPathSearchResponse,
)
def scientific_calculation_path_search(
    calculation_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include_geometries: bool = Query(False),
    include: list[str] | None = Query(None),
    sort: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Return full path-search data for one calculation, paginated by
    point.

    Same handle / pagination / sort / include / 404 contract as
    ``/scan`` and ``/irc``. Calcs with no ``calc_path_search_result``
    row return 404 ``path_search_result_not_found``.

    Pagination applies to the ``points`` array only; per-row energies,
    forces/gradients, ``is_ts_guess`` / ``is_climbing_image`` markers,
    and the per-point ``path_coordinate`` come through verbatim.
    Per-point geometries appear as ``geometry_ref`` only by default;
    ``include_geometries=true`` adds the lightweight ``geometry_link``
    block (ref + natoms + geom_hash). Full coordinate payloads remain
    accessible only via ``GET /scientific/geometries/{geometry_ref}``.

    Internal-ID visibility follows the existing Phase D policy via
    ``include=internal_ids``.
    """
    payload = get_calculation_path_search(
        session,
        calculation_handle=calculation_ref_or_id,
        include_geometries=include_geometries,
        include=parse_include(include),
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(payload)
