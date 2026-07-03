"""Service implementation for /api/v1/scientific/geometries/{geometry_handle}.

Returns the full coordinate payload behind a geometry public ref plus a
compact provenance summary (which calculations produced or consumed
this geometry). Designed as a follow-up read after the search
endpoints expose ``geometry_ref`` handles.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.geometry import Geometry, GeometryAtom
from app.schemas.reads.scientific_geometry import (
    GeometryAtomPayload,
    GeometryProvenance,
    GeometryProvenanceCalcLink,
    GeometryReadRequest,
    RequestEcho,
    ScientificGeometryResponse,
)
from app.services.scientific_read.common import (
    validate_includes,
)
from app.services.scientific_read.handles import resolve_geometry_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "review",
    "provenance",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


def get_geometry(
    session: Session,
    *,
    geometry_handle: str,
    request: GeometryReadRequest,
) -> ScientificGeometryResponse:
    """Resolve *geometry_handle* and return its coordinate payload.

    Path-handle semantics match the rest of the scientific read API:

    - Integer ``geometry.id`` string: SELECT by id.
    - Public ref ``geom_…``: SELECT by ``public_ref``.
    - Wrong prefix: 422 ``handle_type_mismatch``.
    - Malformed: 422 ``invalid_handle``.
    - Missing row: 404.

    The response includes the geometry's natoms / geom_hash, an
    ordered list of atom rows (element + x/y/z, sorted by
    ``atom_index``), the parallel ``symbols`` + ``coords`` shape for
    convenience, the original ``xyz_text`` blob if persisted, and a
    compact provenance summary listing every calculation that
    consumed or produced this geometry.

    :param session: SQLAlchemy session bound to the read DB.
    :param geometry_handle: path string — integer id or ``geom_…`` ref.
    :param request: parsed request model carrying the ``include`` set.
    :raises NotFoundError: 404 when the geometry does not exist.
    :raises ValueError: 422 for malformed or wrong-prefix handles, or
        unknown ``include=`` tokens.
    """
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/geometries/{geometry_handle}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    geometry_id = resolve_geometry_handle(session, geometry_handle)
    geometry = session.get(Geometry, geometry_id)
    if geometry is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError("geometry not found")

    # Hosted abuse-control cap: refuse to materialize huge coordinate
    # payloads anonymously. See
    # ``docs/specs/public_read_abuse_controls.md``.
    cap = settings.max_geometry_atoms_public
    if cap and geometry.natoms is not None and geometry.natoms > cap:
        raise ValueError(
            "geometry_too_large: geometry has "
            f"{geometry.natoms} atoms which exceeds the public cap "
            f"of {cap}. Contact a curator for bulk access."
        )

    atoms = _load_atoms(session, geometry_id)
    provenance = _build_provenance(session, geometry_id)

    # GeometryAtom.element is stored as PostgreSQL ``CHAR(2)``; single-letter
    # symbols come back padded with a trailing space. Strip on read so the
    # public payload is the natural symbol form ("O", "H", "C", …).
    elements = [(a.element or "").strip() for a in atoms]

    return ScientificGeometryResponse(
        request=RequestEcho(include=sorted(includes)),
        geometry_id=geometry.id,
        geometry_ref=geometry.public_ref,
        natoms=geometry.natoms,
        geom_hash=geometry.geom_hash,
        symbols=elements,
        coords=[[a.x, a.y, a.z] for a in atoms],
        atoms=[
            GeometryAtomPayload(
                atom_index=a.atom_index, element=elem, x=a.x, y=a.y, z=a.z
            )
            for a, elem in zip(atoms, elements, strict=False)
        ],
        xyz_text=geometry.xyz_text,
        created_at=geometry.created_at,
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_atoms(session: Session, geometry_id: int) -> list[GeometryAtom]:
    """Return GeometryAtom rows for *geometry_id* ordered by atom_index."""
    return list(
        session.scalars(
            select(GeometryAtom)
            .where(GeometryAtom.geometry_id == geometry_id)
            .order_by(GeometryAtom.atom_index)
        ).all()
    )


def _build_provenance(session: Session, geometry_id: int) -> GeometryProvenance:
    """Build a small produced-by / used-as-input-by cross-reference.

    Each link carries the calculation's public ref, its integer id
    (stripped later if internal_ids isn't allowed), and the calculation
    type. Output links additionally carry the ``CalculationGeometryRole``
    declared at upload time; input links have no role column.
    """
    output_rows = session.execute(
        select(
            CalculationOutputGeometry.calculation_id,
            CalculationOutputGeometry.role,
            Calculation.public_ref,
            Calculation.type,
        )
        .join(
            Calculation,
            Calculation.id == CalculationOutputGeometry.calculation_id,
        )
        .where(CalculationOutputGeometry.geometry_id == geometry_id)
        .order_by(
            CalculationOutputGeometry.calculation_id,
            CalculationOutputGeometry.output_order,
        )
    ).all()
    produced_by = [
        GeometryProvenanceCalcLink(
            calculation_id=row.calculation_id,
            calculation_ref=row.public_ref,
            calculation_type=row.type.value,
            role=row.role.value if row.role is not None else None,
        )
        for row in output_rows
    ]

    input_rows = session.execute(
        select(
            CalculationInputGeometry.calculation_id,
            Calculation.public_ref,
            Calculation.type,
        )
        .join(
            Calculation,
            Calculation.id == CalculationInputGeometry.calculation_id,
        )
        .where(CalculationInputGeometry.geometry_id == geometry_id)
        .order_by(
            CalculationInputGeometry.calculation_id,
            CalculationInputGeometry.input_order,
        )
    ).all()
    used_as_input_by = [
        GeometryProvenanceCalcLink(
            calculation_id=row.calculation_id,
            calculation_ref=row.public_ref,
            calculation_type=row.type.value,
        )
        for row in input_rows
    ]

    return GeometryProvenance(
        produced_by=produced_by,
        used_as_input_by=used_as_input_by,
    )
