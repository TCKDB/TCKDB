"""Read schemas for /api/v1/scientific/geometries/{geometry_handle}.

Detail endpoint that returns the full coordinate payload behind a
``geometry_ref`` returned by ``species-calculations/search`` or other
geometry-bearing scientific responses. Designed as a follow-up read:
search responses identify which geometry was used; this endpoint
delivers the coordinates and a small provenance summary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.reads.scientific_common import CollapseMode


class GeometryReadRequest(BaseModel):
    """Service-layer request model for the geometry detail read.

    The path-parameter ``geometry_handle`` is supplied separately to
    the service function; this model carries only the optional
    ``include=`` set so the service can validate it consistently with
    other scientific reads.
    """

    include: list[str] = Field(default_factory=list)


class RequestEcho(BaseModel):
    """Echo of the parsed query for the geometry detail endpoint.

    The endpoint has no scientific filters, so ``filter`` is always
    ``{}`` and ``sort`` / ``collapse`` are present only for shape
    consistency with the other scientific read envelopes (so callers
    can rely on a stable response top-level).
    """

    filter: dict[str, object] = Field(default_factory=dict)
    sort: str = ""
    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)


class GeometryAtomPayload(BaseModel):
    """One atom row inside a geometry's coordinate payload."""

    atom_index: int
    element: str
    x: float
    y: float
    z: float


class GeometryProvenanceCalcLink(BaseModel):
    """One calculation that consumed or produced this geometry.

    ``role`` is populated only for output links (the
    ``CalculationOutputGeometry.role`` enum) and is always ``None`` for
    input links since ``CalculationInputGeometry`` has no role column.
    """

    calculation_id: int
    calculation_ref: str
    calculation_type: str
    role: str | None = None


class GeometryProvenance(BaseModel):
    """Compact provenance for a geometry detail response.

    ``produced_by`` lists every calculation that emitted this geometry
    as an output (with role); ``used_as_input_by`` lists every
    calculation that consumed it. The v0 endpoint returns the full
    cross-reference set unfiltered — geometries are not usually shared
    across thousands of calculations.
    """

    produced_by: list[GeometryProvenanceCalcLink] = Field(default_factory=list)
    used_as_input_by: list[GeometryProvenanceCalcLink] = Field(default_factory=list)


class ScientificGeometryResponse(BaseModel):
    """Response envelope for ``/api/v1/scientific/geometries/{geometry_handle}``.

    Phase D: ``geometry_id`` is hidden in the default response (Phase D
    internal-id visibility rules apply). ``geometry_ref`` is the
    public stable handle.
    """

    request: RequestEcho
    geometry_id: int
    geometry_ref: str
    natoms: int
    geom_hash: str
    format: Literal["cartesian"] = "cartesian"
    coordinate_units: Literal["angstrom"] = "angstrom"
    symbols: list[str] = Field(default_factory=list)
    coords: list[list[float]] = Field(default_factory=list)
    atoms: list[GeometryAtomPayload] = Field(default_factory=list)
    xyz_text: str | None = None
    created_at: datetime
    provenance: GeometryProvenance = Field(default_factory=GeometryProvenance)
