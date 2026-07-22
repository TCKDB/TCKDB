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

The whole module is built around one contract — *never return a wrong
matrix*. Beyond ``3N`` divisibility and full lower-triangle completeness,
each path applies stricter guards so a truncated, corrupted, or
wrong-orientation block yields ``None`` rather than a plausible-but-wrong
matrix:

* every numeric token must **fully** match a signed Fortran/Gaussian float
  (rejecting an EOF-truncated ``0.999999D-`` that would otherwise parse as
  ``0.999999``);
* ORCA rows must carry exactly the column count implied by the block
  layout, and the assembled matrix must be numerically symmetric (a crashed
  NumFreq leaves a partial, asymmetric ``$hessian`` at full dimension);
* ORCA ``$act_atom`` / ``$act_coord`` markers, when present, must indicate
  the final displacement (a complete Hessian);
* the Gaussian ``opt`` + ``>13 atoms`` + missing ``IOp(2/9=2000)`` case
  (Arkane's standard-orientation trap) is skipped;
* the ORCA ``$atoms`` block is surfaced so the hook can cross-check the
  matrix's frame against the bound geometry's coordinates.
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

#: CODATA bohr radius in Angstrom; ORCA ``$atoms`` coordinates are in bohr.
BOHR_TO_ANGSTROM = 0.52917721067

#: Absolute tolerance for the ORCA full-matrix symmetry check (hartree/bohr²).
_SYMMETRY_ATOL = 1e-6

# A single signed Fortran/Gaussian float. The exponent, when present, is
# mandatory-signed (``D-01``, ``E+00``), which is how a truncated final token
# like ``0.999999D-`` is rejected rather than silently read as ``0.999999``.
_STRICT_FLOAT_SRC = r"[+-]?\d*\.\d+(?:[DdEe][+-]\d+)?"
_STRICT_FLOAT = re.compile(_STRICT_FLOAT_SRC)
# One or more strict floats butted together with no separator — Gaussian's
# fixed-width columns can concatenate (``4.62857243D-07-4.24524320D-07``).
_STRICT_FLOAT_SEQ = re.compile(rf"(?:{_STRICT_FLOAT_SRC})+")


@dataclass(frozen=True)
class ParsedHessian:
    """A Cartesian Hessian recovered from an artifact, in native units.

    ``lower_triangle_hartree_bohr2`` is the packed lower triangle of the
    symmetric ``3N×3N`` matrix *including* the diagonal, row-major, so its
    length is ``N3 * (N3 + 1) // 2`` with ``N3 = 3 * natoms`` — the exact
    on-disk shape of :class:`~app.db.models.calculation.CalculationHessian`.

    ``reference_coords_angstrom`` carries the matrix's own atomic frame when
    the artifact records it (ORCA ``.hess`` ``$atoms``), as ``(element, x, y,
    z)`` in Angstrom. The hook cross-checks it against the bound geometry so
    a coincidental atom-count match against a different molecule or a
    different orientation is caught. ``None`` when the artifact carries no
    coordinates (Gaussian/Molpro logs today).
    """

    natoms: int
    lower_triangle_hartree_bohr2: list[float]
    source: HessianSource
    reference_coords_angstrom: list[tuple[str, float, float, float]] | None = None


def _to_float(token: str) -> float:
    """Parse a Fortran/Gaussian ``D``-exponent float (``0.41D-01``)."""
    return float(token.replace("D", "E").replace("d", "e"))


def _extract_strict_floats(token: str) -> list[float] | None:
    """Return the float(s) in a whitespace token, or ``None`` if malformed.

    The token must be *entirely* one or more valid floats (possibly
    concatenated). Any trailing junk (``0.999999D-``, ``-6.9E-02***``, ``***``)
    fails the full match and rejects the token.
    """
    if not _STRICT_FLOAT_SEQ.fullmatch(token):
        return None
    return [_to_float(m.group()) for m in _STRICT_FLOAT.finditer(token)]


def _row_values(line: str) -> list[float] | None:
    """Classify a matrix line.

    * ``[]``            — a header / column-index line (no numeric values);
    * ``[float, ...]``  — a data row's values, in column order;
    * ``None``          — a malformed data row (reject the whole block).

    Leading label tokens (an integer row index, or a Molpro atom-axis label
    such as ``OX1``) carry no decimal point and are dropped; every remaining
    token must validate as a strict float sequence.
    """
    tokens = line.split()
    i = 0
    while i < len(tokens) and "." not in tokens[i]:
        i += 1
    value_tokens = tokens[i:]
    if not value_tokens:
        return []
    values: list[float] = []
    for token in value_tokens:
        floats = _extract_strict_floats(token)
        if floats is None:
            return None
        values.extend(floats)
    return values


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
    next line is another float-bearing section with no blank separator. A
    malformed numeric token anywhere in the block rejects it.

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
    if header_pos >= len(lines) or _row_values(lines[header_pos]) != []:
        return None
    first_row_pos = header_pos + 1

    # Height of the first block = matrix dimension 3N: consecutive data rows
    # until the next column header (a no-value line), a blank line, or EOF.
    n_rows = 0
    scan = first_row_pos
    while scan < len(lines) and lines[scan].strip():
        classified = _row_values(lines[scan])
        if classified is None:
            return None  # malformed row
        if not classified:
            break  # column header -> end of first block
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
            if pos >= len(lines) or _row_values(lines[pos]) != []:
                return None
            pos += 1
        col0 = b * 5
        expected = min(5, n_rows - col0)
        for r in range(col0, n_rows):
            pos = next_nonblank(pos)
            if pos >= len(lines):
                return None
            values = _row_values(lines[pos])
            if not values:  # header/blank where data expected, or malformed
                return None
            pos += 1
            # A lower-triangular row carries min(expected, r - col0 + 1)
            # entries; anything longer is corruption.
            if len(values) > expected:
                return None
            for k, value in enumerate(values):
                col = col0 + k
                entries[(r, col)] = value
                entries[(col, r)] = value  # symmetric

    packed = _pack_lower_triangle(entries, n_rows)
    if packed is None:
        return None
    return n_rows // 3, packed


def _parse_orca_marker_int(lines: list[str], marker: str) -> int | None:
    """Return the integer on the line after ``marker`` in a ``.hess``."""
    for idx, line in enumerate(lines):
        if line.strip() == marker:
            for probe in lines[idx + 1 :]:
                if probe.strip():
                    try:
                        return int(probe.split()[0])
                    except (ValueError, IndexError):
                        return None
            return None
    return None


def parse_orca_hess_reference_atoms(
    text: str,
) -> list[tuple[str, float, float, float]] | None:
    """Parse the ORCA ``.hess`` ``$atoms`` block into Angstrom coordinates.

    Layout::

        $atoms
        <natoms>
        <element> <mass> <x> <y> <z>   # coordinates in bohr
        ...

    Returns ``[(element, x, y, z), ...]`` in Angstrom, or ``None`` when the
    block is absent or malformed. Surfaced so the hook can cross-check the
    matrix's frame against the bound geometry.
    """
    lines = text.splitlines()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == "$atoms":
            start_idx = idx
    if start_idx is None:
        return None

    pos = start_idx + 1
    while pos < len(lines) and not lines[pos].strip():
        pos += 1
    if pos >= len(lines):
        return None
    try:
        natoms = int(lines[pos].split()[0])
    except (ValueError, IndexError):
        return None
    if natoms <= 0:
        return None
    pos += 1

    atoms: list[tuple[str, float, float, float]] = []
    for _ in range(natoms):
        while pos < len(lines) and not lines[pos].strip():
            pos += 1
        if pos >= len(lines):
            return None
        parts = lines[pos].split()
        pos += 1
        # element mass x y z
        if len(parts) < 5:
            return None
        element = parts[0]
        try:
            x, y, z = (float(parts[2]), float(parts[3]), float(parts[4]))
        except ValueError:
            return None
        atoms.append(
            (
                element,
                x * BOHR_TO_ANGSTROM,
                y * BOHR_TO_ANGSTROM,
                z * BOHR_TO_ANGSTROM,
            )
        )
    return atoms


def parse_orca_hess_force_constants(text: str) -> tuple[int, list[float]] | None:
    """Parse the ``$hessian`` block of an ORCA ``.hess`` file.

    ORCA prints the *full* symmetric matrix as five-column blocks; the line
    immediately after ``$hessian`` is the matrix dimension (``3N``) and each
    data row is ``<row-index> v0 v1 ... v4``. Guards, in service of never
    returning a wrong matrix:

    * each row must carry **exactly** ``min(5, 3N - 5*block)`` numeric values
      (a short row, a ``***``-bearing row, or a lying dimension that runs
      into the next ``$`` section all fail this);
    * the assembled full matrix must be numerically symmetric (a crashed
      NumFreq leaves a partial, asymmetric matrix at full dimension);
    * ``$act_atom`` / ``$act_coord`` markers, when present, must indicate the
      final displacement (``atom == 3N/3 - 1``, ``coord == 2``).

    Returns ``(natoms, packed_lower_triangle)`` in native hartree/bohr²
    units, or ``None`` when the block is absent or fails any guard.
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

    natoms = n_rows // 3

    # Completeness markers: reject a partial NumFreq restart if the last
    # recorded displacement is not the final atom's z coordinate.
    act_atom = _parse_orca_marker_int(lines, "$act_atom")
    act_coord = _parse_orca_marker_int(lines, "$act_coord")
    if act_atom is not None and act_coord is not None:
        if act_atom != natoms - 1 or act_coord != 2:
            return None

    matrix = [[0.0] * n_rows for _ in range(n_rows)]
    n_blocks = math.ceil(n_rows / 5.0)
    for i in range(n_blocks):
        # Consume the column-header line for this block.
        while pos < len(lines) and not lines[pos].strip():
            pos += 1
        if pos >= len(lines):
            return None
        pos += 1
        expected = min(5, n_rows - i * 5)
        for j in range(n_rows):
            while pos < len(lines) and not lines[pos].strip():
                pos += 1
            if pos >= len(lines):
                return None
            values = _row_values(lines[pos])
            pos += 1
            # A full-matrix row must carry exactly the block's column count.
            if values is None or len(values) != expected:
                return None
            for k, value in enumerate(values):
                matrix[j][i * 5 + k] = value

    # Symmetry: free redundancy that also catches partial/restart matrices.
    for r in range(n_rows):
        for c in range(r):
            if abs(matrix[r][c] - matrix[c][r]) > _SYMMETRY_ATOL:
                return None

    packed = [matrix[row][col] for row in range(n_rows) for col in range(row + 1)]
    return natoms, packed


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
            reference_coords_angstrom=parse_orca_hess_reference_atoms(text),
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
