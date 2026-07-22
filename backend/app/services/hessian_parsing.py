"""Re-derive a Cartesian Hessian (force-constant matrix) from an artifact.

Sibling of :mod:`app.services.sp_energy_reconciliation`: pure text in,
structured numbers out, no database dependencies. Where the single-point
module recovers one scalar, this one recovers the symmetric ``3N×3N``
second-derivative matrix of the electronic energy with respect to the
Cartesian nuclear coordinates.

Parsing mirrors RMG-Py/Arkane's ``load_force_constant_matrix`` for each
program, with **one deliberate deviation**: Arkane converts the matrix to
SI (J/m²); TCKDB keeps the program's native atomic units
(hartree/bohr²) and stores the packed lower triangle verbatim, exactly as
the single-point path keeps energies in Hartree. No unit conversion is
applied here.

Three source shapes are handled:

* **Gaussian** — ``Force constants in Cartesian coordinates:`` block in the
  output log (the *last* one wins), printed as a lower-triangular set of
  five-column blocks. ``source = parsed_log``.
* **Molpro** — ``Force Constants (Second Derivatives of the Energy) in
  [a.u.]`` block in the output log, the same lower-triangular five-column
  layout (atom-axis row labels instead of integer indices).
  ``source = parsed_log``.
* **ORCA** — the ``$hessian`` block of a separate ``.hess`` file, a *full*
  (non-triangular) five-column layout whose first line is the matrix
  dimension. A ``.hess`` carries no program banner, so the caller
  dispatches it by artifact *kind*, not by content sniffing.
  ``source = parsed_hess``.

Every recognised matrix is validated for completeness (each lower-triangle
entry present) and for ``3N`` divisibility before it is returned, so a
truncated or malformed block yields ``None`` rather than a wrong matrix.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.db.models.common import HessianSource
from app.services.ess_software_detection import detect_software_from_text

#: Bumped when the parsing/packing contract changes; stored on every row.
HESSIAN_PARSER_VERSION = "hessian_v1"

#: Gaussian's output-log marker for the Cartesian force-constant matrix.
GAUSSIAN_HESSIAN_MARKER = "Force constants in Cartesian coordinates:"
#: Molpro's output-log marker (requires ``print,hessian`` in the deck).
MOLPRO_HESSIAN_MARKER = "Force Constants (Second Derivatives of the Energy) in [a.u.]"

# Matches a single Fortran/Gaussian floating-point value (``0.410282D-01``,
# ``-6.9820446273E-02``, ``0.3700857``). Requires a decimal point, so integer
# column indices and Molpro atom-axis row labels are never matched. Anchoring
# on the sign lets adjacent fixed-width values that Gaussian prints without a
# separating space (``4.62857243D-07-4.24524320D-07``) split cleanly.
_FLOAT_RE = re.compile(r"[-+]?\d*\.\d+(?:[DdEe][-+]?\d+)?")


@dataclass(frozen=True)
class ParsedHessian:
    """A Cartesian Hessian recovered from an artifact, in native units.

    ``lower_triangle_hartree_bohr2`` is the packed lower triangle of the
    symmetric ``3N×3N`` matrix *including* the diagonal, row-major, so its
    length is ``N3 * (N3 + 1) // 2`` with ``N3 = 3 * natoms`` — the exact
    on-disk shape of :class:`~app.db.models.calculation.CalculationHessian`.
    """

    natoms: int
    lower_triangle_hartree_bohr2: list[float]
    source: HessianSource


def _to_float(token: str) -> float:
    """Parse a Fortran/Gaussian ``D``-exponent float (``0.41D-01``)."""
    return float(token.replace("D", "E").replace("d", "e"))


def _row_values(line: str) -> list[float]:
    """Extract every force-constant value on a matrix line, in order.

    Uses :data:`_FLOAT_RE` so the leading row label/index is dropped and
    space-less fixed-width concatenations are still separated correctly.
    A header/column-index line yields an empty list.
    """
    return [_to_float(tok) for tok in _FLOAT_RE.findall(line)]


def _pack_lower_triangle(
    entries: dict[tuple[int, int], float], n_rows: int
) -> list[float] | None:
    """Pack the lower triangle (incl. diagonal) row-major, or ``None``.

    Returns ``None`` when any required lower-triangle cell is missing, which
    is how a truncated block is rejected instead of silently zero-filled.
    """
    packed: list[float] = []
    for row in range(n_rows):
        for col in range(row + 1):
            value = entries.get((row, col))
            if value is None:
                return None
            packed.append(value)
    return packed


def parse_triangular_force_constants(
    text: str, *, marker: str
) -> tuple[int, list[float]] | None:
    """Parse a Gaussian/Molpro lower-triangular Cartesian Hessian block.

    Both programs print the symmetric matrix as consecutive five-column
    blocks: each block opens with a column-header line (no numeric values)
    and then, for block ``b``, lists rows ``b*5 .. 3N-1`` with entries for
    columns ``b*5 .. min(row, b*5 + 4)``. The *last* matching block in the
    text is used (a Gaussian opt+freq log may print several).

    The matrix dimension ``3N`` is taken from the height of the first block,
    then exactly ``ceil(3N/5)`` blocks are read — so parsing stops at the
    true end of the matrix even when (as in a Gaussian freq log) the very
    next line is another float-bearing section with no blank separator.

    Returns ``(natoms, packed_lower_triangle)`` in native hartree/bohr²
    units, or ``None`` when no complete matrix is present.
    """
    lines = text.splitlines()

    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if marker in line:
            start_idx = idx
    if start_idx is None:
        return None

    def next_nonblank(pos: int) -> int:
        while pos < len(lines) and not lines[pos].strip():
            pos += 1
        return pos

    # First block opens with a column-header line (no numeric values).
    header_pos = next_nonblank(start_idx + 1)
    if header_pos >= len(lines) or _row_values(lines[header_pos]):
        return None
    first_row_pos = header_pos + 1

    # Height of the first block = matrix dimension 3N: consecutive data rows
    # until the next column header (a no-value line), a blank line, or EOF.
    n_rows = 0
    scan = first_row_pos
    while scan < len(lines) and lines[scan].strip() and _row_values(lines[scan]):
        n_rows += 1
        scan += 1
    if n_rows == 0 or n_rows % 3 != 0:
        return None

    entries: dict[tuple[int, int], float] = {}
    pos = first_row_pos
    n_blocks = math.ceil(n_rows / 5.0)
    for b in range(n_blocks):
        if b > 0:
            # Consume this block's column-header line.
            pos = next_nonblank(pos)
            if pos >= len(lines) or _row_values(lines[pos]):
                return None
            pos += 1
        col0 = b * 5
        for r in range(col0, n_rows):
            pos = next_nonblank(pos)
            if pos >= len(lines):
                return None
            values = _row_values(lines[pos])
            if not values:
                return None
            pos += 1
            for k, value in enumerate(values):
                col = col0 + k
                entries[(r, col)] = value
                entries[(col, r)] = value  # symmetric

    packed = _pack_lower_triangle(entries, n_rows)
    if packed is None:
        return None
    return n_rows // 3, packed


def parse_orca_hess_force_constants(text: str) -> tuple[int, list[float]] | None:
    """Parse the ``$hessian`` block of an ORCA ``.hess`` file.

    ORCA prints the *full* symmetric matrix as five-column blocks; the line
    immediately after ``$hessian`` is the matrix dimension (``3N``) and each
    data row is ``<row-index> v0 v1 ... v4``. Returns
    ``(natoms, packed_lower_triangle)`` in native hartree/bohr² units, or
    ``None`` when the block is absent or malformed.
    """
    lines = text.splitlines()

    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == "$hessian":
            start_idx = idx
    if start_idx is None:
        return None

    pos = start_idx + 1
    while pos < len(lines) and not lines[pos].strip():
        pos += 1
    if pos >= len(lines):
        return None
    try:
        n_rows = int(lines[pos].split()[0])
    except (ValueError, IndexError):
        return None
    if n_rows <= 0 or n_rows % 3 != 0:
        return None
    pos += 1

    matrix = [[0.0] * n_rows for _ in range(n_rows)]
    n_blocks = math.ceil(n_rows / 5.0)
    for i in range(n_blocks):
        # Consume the column-header line for this block.
        while pos < len(lines) and not lines[pos].strip():
            pos += 1
        if pos >= len(lines):
            return None
        pos += 1
        for j in range(n_rows):
            while pos < len(lines) and not lines[pos].strip():
                pos += 1
            if pos >= len(lines):
                return None
            values = _row_values(lines[pos])
            pos += 1
            for k, value in enumerate(values):
                matrix[j][i * 5 + k] = value

    packed = [matrix[row][col] for row in range(n_rows) for col in range(row + 1)]
    return n_rows // 3, packed


def parse_hessian_from_artifact(
    text: str | None, *, from_hess_file: bool
) -> ParsedHessian | None:
    """Recover a Cartesian Hessian from a decoded artifact's text.

    ``from_hess_file`` selects the ORCA ``.hess`` path (dispatched by
    artifact *kind* because a ``.hess`` has no program banner). Otherwise
    the program is sniffed from the output-log banner: Gaussian and Molpro
    carry the matrix in their logs, ORCA does not (it lives in the separate
    ``.hess``), and an unrecognised banner yields ``None``.
    """
    if not text:
        return None

    if from_hess_file:
        result = parse_orca_hess_force_constants(text)
        if result is None:
            return None
        natoms, packed = result
        return ParsedHessian(
            natoms=natoms,
            lower_triangle_hartree_bohr2=packed,
            source=HessianSource.parsed_hess,
        )

    software = detect_software_from_text(text)
    if software is None:
        return None

    # Local imports keep this module's import surface to the dataclasses;
    # each parser is pure-text and free of DB dependencies. Mirrors the
    # dispatch in ``parse_sp_energy_from_log``.
    if software == "gaussian":
        from app.services.gaussian_parameter_parser import parse_hessian
    elif software == "molpro":
        from app.services.molpro_parameter_parser import parse_hessian
    else:  # orca output logs do not carry the matrix; it is in the .hess
        return None

    result = parse_hessian(text)
    if result is None:
        return None
    natoms, packed = result
    return ParsedHessian(
        natoms=natoms,
        lower_triangle_hartree_bohr2=packed,
        source=HessianSource.parsed_log,
    )
