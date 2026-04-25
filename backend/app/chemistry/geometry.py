from __future__ import annotations

from dataclasses import dataclass

from app.schemas.fragments.geometry import GeometryPayload


@dataclass(frozen=True)
class ParsedXYZ:
    """Parsed canonical representation of an XYZ geometry block.

    :param natoms: Number of atoms declared in the XYZ payload.
    :param canonical_xyz_text: Canonicalized XYZ text used for hashing.
    :param atoms: Parsed atom records as ``(element, x, y, z)`` tuples.
    """

    natoms: int
    canonical_xyz_text: str
    atoms: tuple[tuple[str, float, float, float], ...]


def parse_xyz(payload: GeometryPayload) -> ParsedXYZ:
    """Parse and canonicalize an uploaded XYZ payload.

    :param payload: Upload-facing geometry payload.
    :returns: Parsed XYZ representation with canonicalized coordinate text.
    :raises ValueError: If the XYZ text is malformed or internally inconsistent.
    """

    lines = [line.rstrip() for line in payload.xyz_text.strip().splitlines()]
    if len(lines) < 3:
        raise ValueError("geometry.xyz_text must contain an XYZ header and atom lines")

    try:
        natoms = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError(
            "geometry.xyz_text first line must be an integer atom count"
        ) from exc

    atom_lines = lines[2:]
    if len(atom_lines) != natoms:
        raise ValueError(
            "geometry.xyz_text atom count does not match the number of atom lines"
        )

    atoms: list[tuple[str, float, float, float]] = []
    for line in atom_lines:
        parts = line.split()
        if len(parts) != 4:
            raise ValueError("Each XYZ atom line must contain element x y z")
        element = parts[0]
        try:
            x = float(parts[1])
            y = float(parts[2])
            z = float(parts[3])
        except ValueError as exc:
            raise ValueError("XYZ coordinates must be numeric") from exc
        atoms.append((element, x, y, z))

    canonical_lines = [str(natoms), ""]
    for element, x, y, z in atoms:
        canonical_lines.append(f"{element} {x:.12f} {y:.12f} {z:.12f}")

    return ParsedXYZ(
        natoms=natoms,
        canonical_xyz_text="\n".join(canonical_lines),
        atoms=tuple(atoms),
    )
