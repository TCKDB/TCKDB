"""Read schemas for the specialized scientific path-data endpoints.

These endpoints return full per-point trajectory data for one
calculation, paginating points so search-page-style fan-out can never
trigger an unbounded response. The summary projections under the
calculation detail's heavy includes (``include=scan`` / ``include=irc``
/ ``include=path_search``) carry only result-row aggregates; this
module is where per-point arrays are exposed.

See ``backend/docs/specs/scientific_calculation_path_includes.md``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models.common import CoordinateUnit, IRCDirection
from app.schemas.reads.scientific_calculation import (
    CalculationCoreBlock,
    CalculationIRCSummary,
    CalculationOwnerSummary,
    CalculationPathSearchSummary,
    CalculationScanSummary,
    ScanCoordinateSummary,
)
from app.schemas.reads.scientific_common import Pagination


# ---------------------------------------------------------------------------
# Request echo
# ---------------------------------------------------------------------------


class ScanRequestEcho(BaseModel):
    """Echoes the parsed knobs the caller supplied to ``/scan``.

    Mirrors the convention used by other ``/scientific/*`` envelopes
    (``request.include`` etc.) so callers can confirm the resolved
    filter / pagination values. ``include`` carries opt-in tokens
    such as ``internal_ids`` that flow through the existing Phase D
    visibility policy via ``apply_internal_ids_visibility``.
    """

    include_geometries: bool = False
    include: list[str] = Field(default_factory=list)
    sort: str = ""
    offset: int = 0
    limit: int = 0


# ---------------------------------------------------------------------------
# Per-point shapes
# ---------------------------------------------------------------------------


class ScanPointCoordinateValueSummary(BaseModel):
    """One coordinate-value entry for a single scan point.

    Mirrors the ORM ``calc_scan_point_coordinate_value`` row stripped
    of database surrogates. ``coordinate_index`` and the value itself
    are scientific metadata and stay visible regardless of internal-id
    policy.
    """

    coordinate_index: int
    coordinate_value: float
    value_unit: CoordinateUnit | None = None


class PointGeometryLink(BaseModel):
    """Lightweight geometry-link projection shared by every per-point
    path-data endpoint (``/scan``, ``/irc``, future ``/path-search``).

    Used when ``include_geometries=true``. Carries ``geometry_ref``
    plus the cheap metadata the geometry table can hand back without
    inlining XYZ coordinates. Full coordinate data lives behind
    ``GET /scientific/geometries/{geometry_ref}`` — this schema
    deliberately stops short of inlining ``xyz_text``, atom rows, or
    coordinate arrays.
    """

    geometry_id: int | None = None
    geometry_ref: str
    natoms: int | None = None
    geom_hash: str | None = None


class ScanPointDetail(BaseModel):
    """One scan point with its energy projection, geometry link, and
    coordinate-value list.

    ``geometry_id`` is subject to the Phase D internal-ID visibility
    policy. ``geometry_ref`` is always present when the underlying
    geometry exists. The ``geometry_link`` field is populated only
    when the caller passes ``include_geometries=true``; otherwise the
    point carries the bare ``geometry_ref`` (and policy-gated
    ``geometry_id``).
    """

    point_index: int
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    note: str | None = None

    geometry_id: int | None = None
    geometry_ref: str | None = None
    geometry_link: PointGeometryLink | None = None

    coordinate_values: list[ScanPointCoordinateValueSummary] = Field(
        default_factory=list
    )


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------


class IRCRequestEcho(BaseModel):
    """Echoes the parsed knobs the caller supplied to ``/irc``.

    Same shape as :class:`ScanRequestEcho` so a generic client parser
    can handle every per-point path-data endpoint with one set of code.
    """

    include_geometries: bool = False
    include: list[str] = Field(default_factory=list)
    sort: str = ""
    offset: int = 0
    limit: int = 0


class IRCPointDetail(BaseModel):
    """One IRC point with its trajectory state, energy projection,
    gradient norms, and geometry link.

    Mirrors :class:`ScanPointDetail` shape conventions: bare
    ``geometry_ref`` always (when the underlying geometry exists),
    ``geometry_id`` policy-gated, optional ``geometry_link`` block
    only when the caller passed ``include_geometries=true``.

    ``direction`` may be ``None`` for ORCA-style TS marker rows.
    ``is_ts`` is the per-row TS flag (Gaussian point 0 / ORCA
    ``<= TS`` marker). ``reaction_coordinate`` carries the algorithm's
    reaction-coordinate scalar for the point (signed for forward /
    reverse).
    """

    point_index: int
    direction: IRCDirection | None = None
    is_ts: bool
    reaction_coordinate: float | None = None
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    max_gradient: float | None = None
    rms_gradient: float | None = None
    note: str | None = None

    geometry_id: int | None = None
    geometry_ref: str | None = None
    geometry_link: PointGeometryLink | None = None


class ScientificCalculationIRCResponse(BaseModel):
    """Full-data IRC response for one calculation.

    Reuses ``CalculationCoreBlock`` / ``CalculationOwnerSummary`` /
    ``CalculationIRCSummary`` from the detail-endpoint schemas — the
    ``irc`` block is byte-for-byte the same shape that
    ``include=irc`` on the detail endpoint produces, so a caller
    already parsing that include can reuse the same parsing code for
    everything except the new ``points`` array.

    Pagination applies to ``points`` only.
    """

    request: IRCRequestEcho
    calculation: CalculationCoreBlock
    owner: CalculationOwnerSummary
    irc: CalculationIRCSummary
    points: list[IRCPointDetail]
    pagination: Pagination


class PathSearchRequestEcho(BaseModel):
    """Echoes the parsed knobs the caller supplied to ``/path-search``.

    Same shape as :class:`ScanRequestEcho` / :class:`IRCRequestEcho` so
    a generic client parser can handle every per-point path-data
    endpoint with one set of code.
    """

    include_geometries: bool = False
    include: list[str] = Field(default_factory=list)
    sort: str = ""
    offset: int = 0
    limit: int = 0


class PathSearchPointDetail(BaseModel):
    """One path-search point with its trajectory state, energy
    projection, force/gradient norms, marker flags, and geometry link.

    Mirrors :class:`ScanPointDetail` / :class:`IRCPointDetail` shape
    conventions: bare ``geometry_ref`` always (when the underlying
    geometry exists), ``geometry_id`` policy-gated, optional
    ``geometry_link`` block only when the caller passed
    ``include_geometries=true``.

    ``is_ts_guess`` and ``is_climbing_image`` are two independent
    per-point marker flags: ``is_ts_guess`` is the algorithm's own
    picked TS candidate; ``is_climbing_image`` is the NEB
    climbing-image flag. The two can overlap (NEB usually sets both on
    the climbing image) but are conceptually distinct, so the public
    detail keeps them separate.
    """

    point_index: int

    path_coordinate: float | None = None
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    max_force: float | None = None
    rms_force: float | None = None
    max_gradient: float | None = None
    rms_gradient: float | None = None

    is_ts_guess: bool = False
    is_climbing_image: bool = False
    note: str | None = None

    geometry_id: int | None = None
    geometry_ref: str | None = None
    geometry_link: PointGeometryLink | None = None


class ScientificCalculationPathSearchResponse(BaseModel):
    """Full-data path-search response for one calculation.

    Reuses ``CalculationCoreBlock`` / ``CalculationOwnerSummary`` /
    ``CalculationPathSearchSummary`` from the detail-endpoint schemas
    so a caller already parsing ``include=path_search`` can reuse
    exactly the same parsing code for everything except the new
    ``points`` array.

    Pagination applies to ``points`` only.
    """

    request: PathSearchRequestEcho
    calculation: CalculationCoreBlock
    owner: CalculationOwnerSummary
    path_search: CalculationPathSearchSummary
    points: list[PathSearchPointDetail]
    pagination: Pagination


class ScientificCalculationScanResponse(BaseModel):
    """Full-data scan response for one calculation.

    Reuses ``CalculationCoreBlock`` / ``CalculationOwnerSummary`` /
    ``CalculationScanSummary`` / ``ScanCoordinateSummary`` from the
    detail-endpoint schemas so a caller already parsing
    ``include=scan`` can reuse exactly the same parsing code for
    everything except the new ``points`` array.

    Pagination applies to ``points`` only — coordinates are not
    paginated (the schema bound on ``calc_scan_result.dimension`` keeps
    the coordinate list small).
    """

    request: ScanRequestEcho
    calculation: CalculationCoreBlock
    owner: CalculationOwnerSummary
    scan: CalculationScanSummary
    coordinates: list[ScanCoordinateSummary]
    points: list[ScanPointDetail]
    pagination: Pagination


__all__ = [
    "IRCPointDetail",
    "IRCRequestEcho",
    "PathSearchPointDetail",
    "PathSearchRequestEcho",
    "PointGeometryLink",
    "ScanPointCoordinateValueSummary",
    "ScanPointDetail",
    "ScanRequestEcho",
    "ScientificCalculationIRCResponse",
    "ScientificCalculationPathSearchResponse",
    "ScientificCalculationScanResponse",
]
