"""Service implementations for the specialized scientific path-data
endpoints.

Each endpoint here is the per-calc URL that backs a heavy-include
summary on the detail/search surface — the includes return bounded
aggregates while these endpoints return the full per-point arrays
behind a paginated, abuse-bound URL. The split keeps the search
endpoint safe (no fan-out over thousands of points per record) while
still giving callers a way to fetch the trajectory when they actually
need it.

See ``backend/docs/specs/scientific_calculation_path_includes.md``.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationPathSearchPoint,
    CalculationPathSearchResult,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
    CalculationScanResult,
)
from app.db.models.geometry import Geometry
from app.schemas.reads.scientific_calculation import (
    CalculationCoreBlock,
    CalculationIRCSummary,
    CalculationScanSummary,
)
from app.schemas.reads.scientific_calculation_paths import (
    IRCPointDetail,
    IRCRequestEcho,
    PathSearchPointDetail,
    PathSearchRequestEcho,
    PointGeometryLink,
    ScanPointCoordinateValueSummary,
    ScanPointDetail,
    ScanRequestEcho,
    ScientificCalculationIRCResponse,
    ScientificCalculationPathSearchResponse,
    ScientificCalculationScanResponse,
)
from app.services.scientific_read.calculations import (
    _build_irc_include_summary,
    _build_owner,
    _build_path_search_include_summary,
    _build_scan_include_summary,
    _load_review_badge,
)
from app.services.scientific_read.common import (
    build_pagination,
    reject_client_sort,
    validate_includes,
    validate_pagination,
)
from app.services.scientific_read.handles import resolve_calculation_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)


# Path-data endpoints share an include policy: only ``internal_ids``
# (and the ``all`` shorthand) is legal. ``include_geometries`` is its
# own boolean knob; the include set is reserved for visibility opt-ins
# like the Phase D internal-ID policy.
_PATH_LEGAL_INCLUDE_TOKENS: set[str] = {"internal_ids", "all"}
_PATH_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

# Aliases preserved for backward compatibility with the scan loader's
# original constant names — both endpoints share the same policy.
_SCAN_LEGAL_INCLUDE_TOKENS = _PATH_LEGAL_INCLUDE_TOKENS
_SCAN_INTERNAL_INCLUDE_TOKENS = _PATH_INTERNAL_INCLUDE_TOKENS


def get_calculation_scan(
    session: Session,
    *,
    calculation_handle: str,
    include_geometries: bool = False,
    include: list[str] | None = None,
    sort: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> ScientificCalculationScanResponse:
    """Return full scan data for one calculation, paginated by point.

    Path-handle semantics match the rest of the scientific read API:

    - Integer ``calculation.id`` string: SELECT by id.
    - Public ref ``calc_…``: SELECT by ``public_ref``.
    - Wrong prefix: 422 ``handle_type_mismatch``.
    - Malformed: 422 ``invalid_handle``.
    - Missing calc: 404.
    - Calc exists but no ``calc_scan_result`` row: 404
      ``scan_result_not_found`` (matches the legacy
      ``/api/v1/calculations/{id}/scan-result`` semantics).

    The response reuses the calculation core block + owner summary +
    scan summary from the detail endpoint, so a caller already
    parsing ``include=scan`` can reuse the same parsing for everything
    except the new ``points`` array. **Per-point geometries** are
    surfaced as ``geometry_ref`` only by default; passing
    ``include_geometries=true`` adds a lightweight ``geometry_link``
    block (ref + natoms + geom_hash). Full coordinate payloads still
    live behind ``GET /scientific/geometries/{geometry_ref}`` —
    XYZ text and atom rows are never inlined here.

    :raises NotFoundError: 404 when the calculation does not exist
        or has no scan-result row.
    :raises ValueError: 422 for malformed/wrong-prefix handles, sort
        rejection, or pagination overrun.
    """
    reject_client_sort(sort)
    offset, limit = validate_pagination(offset, limit)
    includes = validate_includes(
        include or [],
        _SCAN_LEGAL_INCLUDE_TOKENS,
        "/scientific/calculations/{calculation_ref_or_id}/scan",
        internal_tokens=_SCAN_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    calculation_id = resolve_calculation_handle(session, calculation_handle)
    calc = session.get(Calculation, calculation_id)
    if calc is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"calculation not found (calculation_id={calculation_id})",
            code="handle_not_found",
        )

    scan_summary = _build_scan_include_summary(session, calculation_id)
    if scan_summary is None:
        raise NotFoundError(
            f"scan result not found for calculation {calc.public_ref}",
            code="scan_result_not_found",
        )

    badge = _load_review_badge(session, calculation_id)
    owner = _build_owner(session, calc)

    # Full ordered coordinate list (not paginated; bounded by
    # calc_scan_result.dimension which the schema keeps small).
    coordinates = scan_summary.coordinates

    # Pre-pagination total over scan points.
    total_points = scan_summary.point_count

    point_rows = session.execute(
        select(CalculationScanPoint)
        .where(CalculationScanPoint.calculation_id == calculation_id)
        .order_by(CalculationScanPoint.point_index.asc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    point_indices = [row.point_index for row in point_rows]
    coord_values_by_point = _load_coord_values(
        session, calculation_id, point_indices
    )

    geometry_ids = {
        row.geometry_id for row in point_rows if row.geometry_id is not None
    }
    geometry_meta_by_id = (
        _load_geometry_metadata(session, geometry_ids)
        if geometry_ids
        else {}
    )

    points = [
        _build_point_detail(
            row,
            coord_values=coord_values_by_point.get(row.point_index, []),
            geometry_meta=(
                geometry_meta_by_id.get(row.geometry_id)
                if row.geometry_id is not None
                else None
            ),
            include_geometries=include_geometries,
        )
        for row in point_rows
    ]

    return ScientificCalculationScanResponse(
        request=ScanRequestEcho(
            include_geometries=include_geometries,
            include=sorted(includes),
            sort="point_index",
            offset=offset,
            limit=limit,
        ),
        calculation=CalculationCoreBlock(
            calculation_id=calc.id,
            calculation_ref=calc.public_ref,
            type=calc.type,
            quality=calc.quality,
            created_at=calc.created_at,
            review=badge,
        ),
        owner=owner,
        scan=scan_summary,
        coordinates=coordinates,
        points=points,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(points),
            total=total_points,
        ),
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_coord_values(
    session: Session,
    calculation_id: int,
    point_indices: list[int],
) -> dict[int, list[ScanPointCoordinateValueSummary]]:
    """Bulk-load coordinate values for a page of scan points.

    Returns a mapping ``{point_index: [ScanPointCoordinateValueSummary, ...]}``
    so the per-point serializer can attach values without an N+1.
    """
    if not point_indices:
        return {}
    rows = session.execute(
        select(CalculationScanPointCoordinateValue)
        .where(
            CalculationScanPointCoordinateValue.calculation_id == calculation_id,
            CalculationScanPointCoordinateValue.point_index.in_(point_indices),
        )
        .order_by(
            CalculationScanPointCoordinateValue.point_index.asc(),
            CalculationScanPointCoordinateValue.coordinate_index.asc(),
        )
    ).scalars().all()

    out: dict[int, list[ScanPointCoordinateValueSummary]] = {
        idx: [] for idx in point_indices
    }
    for row in rows:
        out.setdefault(row.point_index, []).append(
            ScanPointCoordinateValueSummary(
                coordinate_index=row.coordinate_index,
                coordinate_value=row.coordinate_value,
                value_unit=row.value_unit,
            )
        )
    return out


def _load_geometry_metadata(
    session: Session,
    geometry_ids: set[int],
) -> dict[int, PointGeometryLink]:
    """Bulk-load lightweight geometry metadata for the page.

    Carries ref + natoms + geom_hash only. Full coordinate data is
    intentionally not loaded — that lives behind
    ``/scientific/geometries/{geometry_ref}``.
    """
    rows = session.execute(
        select(
            Geometry.id,
            Geometry.public_ref,
            Geometry.natoms,
            Geometry.geom_hash,
        ).where(Geometry.id.in_(geometry_ids))
    ).all()
    return {
        row.id: PointGeometryLink(
            geometry_id=row.id,
            geometry_ref=row.public_ref,
            natoms=row.natoms,
            geom_hash=row.geom_hash,
        )
        for row in rows
    }


def _build_point_detail(
    row: CalculationScanPoint,
    *,
    coord_values: list[ScanPointCoordinateValueSummary],
    geometry_meta: PointGeometryLink | None,
    include_geometries: bool,
) -> ScanPointDetail:
    """Project one scan-point row into the public detail shape."""
    geometry_ref = geometry_meta.geometry_ref if geometry_meta else None
    geometry_link = (
        geometry_meta if include_geometries and geometry_meta is not None else None
    )
    return ScanPointDetail(
        point_index=row.point_index,
        electronic_energy_hartree=row.electronic_energy_hartree,
        relative_energy_kj_mol=row.relative_energy_kj_mol,
        note=row.note,
        geometry_id=row.geometry_id,
        geometry_ref=geometry_ref,
        geometry_link=geometry_link,
        coordinate_values=coord_values,
    )


# ---------------------------------------------------------------------------
# /irc loader
# ---------------------------------------------------------------------------


def get_calculation_irc(
    session: Session,
    *,
    calculation_handle: str,
    include_geometries: bool = False,
    include: list[str] | None = None,
    sort: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> ScientificCalculationIRCResponse:
    """Return full IRC data for one calculation, paginated by point.

    Same handle / pagination / sort / include / 404 contract as
    :func:`get_calculation_scan`. Calculations with no
    ``calc_irc_result`` row return 404 ``irc_result_not_found``.

    The ``irc`` block is byte-for-byte the same shape ``include=irc``
    on the detail endpoint produces; per-point arrays live in the
    paginated ``points`` array. Geometries follow the same ref-only-
    by-default contract: passing ``include_geometries=true`` adds a
    lightweight ``geometry_link`` block per point — never inlines XYZ.

    :raises NotFoundError: 404 when the calculation does not exist
        or has no IRC-result row.
    :raises ValueError: 422 for malformed/wrong-prefix handles, sort
        rejection, pagination overrun, or unknown include tokens.
    """
    reject_client_sort(sort)
    offset, limit = validate_pagination(offset, limit)
    includes = validate_includes(
        include or [],
        _PATH_LEGAL_INCLUDE_TOKENS,
        "/scientific/calculations/{calculation_ref_or_id}/irc",
        internal_tokens=_PATH_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    calculation_id = resolve_calculation_handle(session, calculation_handle)
    calc = session.get(Calculation, calculation_id)
    if calc is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"calculation not found (calculation_id={calculation_id})",
            code="handle_not_found",
        )

    irc_summary = _build_irc_include_summary(session, calculation_id)
    if irc_summary is None:
        raise NotFoundError(
            f"IRC result not found for calculation {calc.public_ref}",
            code="irc_result_not_found",
        )

    badge = _load_review_badge(session, calculation_id)
    owner = _build_owner(session, calc)

    # Total = the same aggregate the include summary reports for
    # ``stored_point_count``-style access. ``calc_irc_result.point_count``
    # is the producer-reported count and may differ; the actual
    # ``COUNT(*)`` over the point table is the right pagination total.
    total_points = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationIRCPoint)
            .where(CalculationIRCPoint.calculation_id == calculation_id)
        )
        or 0
    )

    point_rows = session.execute(
        select(CalculationIRCPoint)
        .where(CalculationIRCPoint.calculation_id == calculation_id)
        .order_by(CalculationIRCPoint.point_index.asc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    geometry_ids = {
        row.geometry_id for row in point_rows if row.geometry_id is not None
    }
    geometry_meta_by_id = (
        _load_geometry_metadata(session, geometry_ids)
        if geometry_ids
        else {}
    )

    points = [
        _build_irc_point_detail(
            row,
            geometry_meta=(
                geometry_meta_by_id.get(row.geometry_id)
                if row.geometry_id is not None
                else None
            ),
            include_geometries=include_geometries,
        )
        for row in point_rows
    ]

    return ScientificCalculationIRCResponse(
        request=IRCRequestEcho(
            include_geometries=include_geometries,
            include=sorted(includes),
            sort="point_index",
            offset=offset,
            limit=limit,
        ),
        calculation=CalculationCoreBlock(
            calculation_id=calc.id,
            calculation_ref=calc.public_ref,
            type=calc.type,
            quality=calc.quality,
            created_at=calc.created_at,
            review=badge,
        ),
        owner=owner,
        irc=irc_summary,
        points=points,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(points),
            total=total_points,
        ),
    )


def _build_irc_point_detail(
    row: CalculationIRCPoint,
    *,
    geometry_meta: PointGeometryLink | None,
    include_geometries: bool,
) -> IRCPointDetail:
    """Project one IRC-point row into the public detail shape."""
    geometry_ref = geometry_meta.geometry_ref if geometry_meta else None
    geometry_link = (
        geometry_meta if include_geometries and geometry_meta is not None else None
    )
    return IRCPointDetail(
        point_index=row.point_index,
        direction=row.direction,
        is_ts=row.is_ts,
        reaction_coordinate=row.reaction_coordinate,
        electronic_energy_hartree=row.electronic_energy_hartree,
        relative_energy_kj_mol=row.relative_energy_kj_mol,
        max_gradient=row.max_gradient,
        rms_gradient=row.rms_gradient,
        note=row.note,
        geometry_id=row.geometry_id,
        geometry_ref=geometry_ref,
        geometry_link=geometry_link,
    )


# ---------------------------------------------------------------------------
# /path-search loader
# ---------------------------------------------------------------------------


def get_calculation_path_search(
    session: Session,
    *,
    calculation_handle: str,
    include_geometries: bool = False,
    include: list[str] | None = None,
    sort: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> ScientificCalculationPathSearchResponse:
    """Return full path-search data for one calculation, paginated by
    point.

    Same handle / pagination / sort / include / 404 contract as
    :func:`get_calculation_scan` and :func:`get_calculation_irc`.
    Calculations with no ``calc_path_search_result`` row return 404
    ``path_search_result_not_found``.

    The ``path_search`` block is byte-for-byte the same shape
    ``include=path_search`` on the detail endpoint produces; per-point
    arrays live in the paginated ``points`` array. Geometries follow
    the same ref-only-by-default contract: passing
    ``include_geometries=true`` adds a lightweight ``geometry_link``
    block per point — never inlines XYZ.

    :raises NotFoundError: 404 when the calculation does not exist
        or has no path-search-result row.
    :raises ValueError: 422 for malformed/wrong-prefix handles, sort
        rejection, pagination overrun, or unknown include tokens.
    """
    reject_client_sort(sort)
    offset, limit = validate_pagination(offset, limit)
    includes = validate_includes(
        include or [],
        _PATH_LEGAL_INCLUDE_TOKENS,
        "/scientific/calculations/{calculation_ref_or_id}/path-search",
        internal_tokens=_PATH_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    calculation_id = resolve_calculation_handle(session, calculation_handle)
    calc = session.get(Calculation, calculation_id)
    if calc is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"calculation not found (calculation_id={calculation_id})",
            code="handle_not_found",
        )

    path_search_summary = _build_path_search_include_summary(
        session, calculation_id
    )
    if path_search_summary is None:
        raise NotFoundError(
            f"path-search result not found for calculation "
            f"{calc.public_ref}",
            code="path_search_result_not_found",
        )

    badge = _load_review_badge(session, calculation_id)
    owner = _build_owner(session, calc)

    # Pagination total comes from a fresh COUNT(*) on the point table.
    # ``calc_path_search_result.n_points`` is the producer-reported
    # value and may diverge from the actual stored count; the COUNT(*)
    # is the right total for pagination semantics.
    total_points = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationPathSearchPoint)
            .where(CalculationPathSearchPoint.calculation_id == calculation_id)
        )
        or 0
    )

    point_rows = session.execute(
        select(CalculationPathSearchPoint)
        .where(CalculationPathSearchPoint.calculation_id == calculation_id)
        .order_by(CalculationPathSearchPoint.point_index.asc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    geometry_ids = {
        row.geometry_id for row in point_rows if row.geometry_id is not None
    }
    geometry_meta_by_id = (
        _load_geometry_metadata(session, geometry_ids)
        if geometry_ids
        else {}
    )

    points = [
        _build_path_search_point_detail(
            row,
            geometry_meta=(
                geometry_meta_by_id.get(row.geometry_id)
                if row.geometry_id is not None
                else None
            ),
            include_geometries=include_geometries,
        )
        for row in point_rows
    ]

    return ScientificCalculationPathSearchResponse(
        request=PathSearchRequestEcho(
            include_geometries=include_geometries,
            include=sorted(includes),
            sort="point_index",
            offset=offset,
            limit=limit,
        ),
        calculation=CalculationCoreBlock(
            calculation_id=calc.id,
            calculation_ref=calc.public_ref,
            type=calc.type,
            quality=calc.quality,
            created_at=calc.created_at,
            review=badge,
        ),
        owner=owner,
        path_search=path_search_summary,
        points=points,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(points),
            total=total_points,
        ),
    )


def _build_path_search_point_detail(
    row: CalculationPathSearchPoint,
    *,
    geometry_meta: PointGeometryLink | None,
    include_geometries: bool,
) -> PathSearchPointDetail:
    """Project one path-search-point row into the public detail shape."""
    geometry_ref = geometry_meta.geometry_ref if geometry_meta else None
    geometry_link = (
        geometry_meta if include_geometries and geometry_meta is not None else None
    )
    return PathSearchPointDetail(
        point_index=row.point_index,
        path_coordinate=row.path_coordinate,
        electronic_energy_hartree=row.electronic_energy_hartree,
        relative_energy_kj_mol=row.relative_energy_kj_mol,
        max_force=row.max_force,
        rms_force=row.rms_force,
        max_gradient=row.max_gradient,
        rms_gradient=row.rms_gradient,
        is_ts_guess=row.is_ts_guess,
        is_climbing_image=row.is_climbing_image,
        note=row.note,
        geometry_id=row.geometry_id,
        geometry_ref=geometry_ref,
        geometry_link=geometry_link,
    )


__all__ = [
    "get_calculation_irc",
    "get_calculation_path_search",
    "get_calculation_scan",
]
