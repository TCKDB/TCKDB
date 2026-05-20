"""Builder: CCCBDB geometry record → ``GeometryPayload`` dict.

The existing ``tckdb_schemas.fragments.geometry.GeometryPayload`` has a
single required field, ``xyz_text``. This builder serializes the
Phase 1 atom list into canonical XYZ text:

    <natoms>
    CCCBDB experimental geometry (<source_url>)
    <element> <x> <y> <z>
    ...

Coordinates are already in ångström at Phase 1 (the parser converts
through :mod:`app.importers.cccbdb.normalizers.units`), so the builder
only formats them.
"""

from __future__ import annotations

from typing import Any

from app.importers.cccbdb.models import CCCBDBExperimentalSpeciesRecord


def build_geometry_payload(
    record: CCCBDBExperimentalSpeciesRecord,
) -> dict[str, Any] | None:
    """Return a ``GeometryPayload``-compatible dict or ``None``.

    ``None`` is returned when the parser did not find a geometry
    section. The builder never fabricates coordinates.
    """

    geometry = record.geometry
    if geometry is None or not geometry.atoms:
        return None

    natoms = len(geometry.atoms)
    comment = f"CCCBDB experimental geometry ({record.source_metadata.source_url})"
    lines = [str(natoms), comment]
    for atom in geometry.atoms:
        lines.append(
            f"{atom.element} "
            f"{atom.x_angstrom:.6f} "
            f"{atom.y_angstrom:.6f} "
            f"{atom.z_angstrom:.6f}"
        )
    # ``GeometryPayload`` accepts only ``xyz_text`` (extra fields are
    # forbidden), so the atom count lives on line 0 of the XYZ block
    # — readers can parse it back if they need it.
    return {"xyz_text": "\n".join(lines) + "\n"}
