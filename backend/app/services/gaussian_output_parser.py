"""Parse results from Gaussian output (log) files.

Extracts the final optimized geometry from Standard/Input orientation blocks.
Returns atoms in the format expected by ``resolve_atom_mapping()``:
``tuple[tuple[str, float, float, float], ...]``.

Future: energy, frequency, ZPE, timing extraction can be added here.
"""

from __future__ import annotations

from rdkit import Chem

_pt = Chem.GetPeriodicTable()


def extract_final_geometry(
    lines: list[str],
) -> tuple[tuple[str, float, float, float], ...]:
    """Extract the last geometry from a Gaussian log file.

    Strategy:
    1. Search backwards for the last "Standard orientation:" block (preferred).
    2. Fall back to the last "Input orientation:" block.

    :param lines: Raw lines from the Gaussian log file.
    :returns: Tuple of (element_symbol, x, y, z) for each atom.
    :raises ValueError: If no parsable geometry block is found.
    """
    # --- Try last "Standard orientation:" block ---
    result = _parse_last_orientation_block(lines, "Standard orientation:")
    if result is not None:
        return result

    # --- Fall back to last "Input orientation:" block ---
    result = _parse_last_orientation_block(lines, "Input orientation:")
    if result is not None:
        return result

    raise ValueError(
        "No parsable 'Standard orientation' or 'Input orientation' "
        "geometry block found in log file."
    )


def extract_final_geometry_from_file(
    path: str,
) -> tuple[tuple[str, float, float, float], ...]:
    """Convenience wrapper that reads a log file and extracts the final geometry."""
    with open(path) as f:
        lines = f.readlines()
    return extract_final_geometry(lines)


def _parse_last_orientation_block(
    lines: list[str],
    header: str,
) -> tuple[tuple[str, float, float, float], ...] | None:
    """Parse the last occurrence of an orientation block.

    Scans backwards to find the last *header* line, then parses the atom
    table that follows (5 header lines after the header, then data rows
    until a dashed separator or empty line).
    """
    for i in range(len(lines) - 1, -1, -1):
        if header in lines[i]:
            atoms = _parse_orientation_table(lines, i + 5)
            if atoms:
                return tuple(atoms)
            break  # found header but couldn't parse — don't keep searching
    return None


def _parse_orientation_table(
    lines: list[str],
    start: int,
) -> list[tuple[str, float, float, float]]:
    """Parse atom rows from a Gaussian orientation table.

    Each row has the format::

        Center  Atomic  Atomic    Coordinates (Angstroms)
        Number  Number   Type       X           Y           Z
        ----------------------------------------------------------
             1       7       0     1.507859   -1.030328    0.252685

    :param lines: Full log file lines.
    :param start: Index of the first data row (after the 5-line header).
    """
    atoms: list[tuple[str, float, float, float]] = []
    j = start
    while j < len(lines):
        line = lines[j]
        stripped = line.strip()

        # Stop at empty lines or dashed separators
        if not stripped or "-----" in stripped:
            break

        parts = line.split()
        # Valid atom row: center_num, atomic_num, type, x, y, z (6+ columns)
        if parts and parts[0].isdigit() and len(parts) >= 6:
            atomic_number = int(parts[1])
            symbol = _pt.GetElementSymbol(atomic_number)
            x, y, z = float(parts[3]), float(parts[4]), float(parts[5])
            atoms.append((symbol, x, y, z))

        j += 1

    return atoms
