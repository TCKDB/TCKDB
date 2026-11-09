"""Read the Cartesian Hessian from a Gaussian log's archive block.

For larger molecules (14 atoms and up in the E2 study) Gaussian 16 prints no ``Force constants in Cartesian
coordinates`` block, but the archive block at the end of the log carries the same matrix. Pure text in, numbers out,
like :mod:`app.services.hessian_parsing`.

Format, measured on real logs (ethanethiol and n-butane wB97X-D/def2-TZVP opt+freq NoSymm; a UwB97XD/def2TZVP G09
freq log of a 12-atom radical; a CBS-QB3 G09 log):

* The archive is a run of 70-column lines that opens with `` 1\\1\\GINC-`` and closes at ``\\@``. Values and
  fields are wrapped anywhere, mid-number included. Rejoin by dropping the one leading space of every line and
  concatenating; do not insert anything at a line break.
* Joined, the sections are separated by ``\\\\``: ``1\\1\\GINC-...``, route, title, charge/multiplicity plus the
  atoms, then a section that holds ``Version=...\\HF=...\\...\\NImag=0`` (single-backslash fields), then the
  Hessian, then the gradient (``3N`` values), then the closing ``@``. The Hessian is **the section after the one
  containing ``NImag=``**.
* The Hessian is the packed lower triangle, row-major, diagonal included: ``3N(3N+1)/2`` comma-separated values
  (903 for the 14-atom n-butane, 378 for 8-atom ethanethiol).
* Units are hartree/bohr^2, the same as the force-constant block. On the three logs that print both, the two
  blocks agree element for element to within the force-constant block's print precision (six significant
  figures; largest difference 5e-7), in the same atom order and frame.
* Values are printed to eight decimals, absolute.
* An ``opt`` + ``freq`` log holds one archive block per job step (``FOpt``, then ``Freq``); only the one with an
  ``NImag=`` section followed by a Hessian counts, and the last such block wins, as for the force-constant block.

Orientation. The archive's atoms are written in the frame the Hessian is in. Rather than assume which frame that is,
this module finds the archive atoms in the log's own ``Input orientation`` table (the last one printed) and
accepts the Hessian only when they coincide. A log whose archive atoms are only in the ``Standard orientation``
table, or in neither, is refused with ``archive_orientation_unverified``: a symmetry-reoriented matrix bound to an
input-orientation geometry is a wrong matrix that looks right. The archive atoms are returned as the matrix's
reference frame, so the caller's frame check against the bound geometry runs as it does for an ORCA ``.hess``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.db.models.common import HessianSource
from app.services.hessian_parsing import ParsedHessian

#: Reason tokens, the ``reason`` of a ``hessian_not_stored_unparseable`` warning.
REASON_ARCHIVE_UNREADABLE = "archive_block_unreadable"
REASON_ARCHIVE_ORIENTATION = "archive_orientation_unverified"

#: Tolerance (Angstrom) for finding the archive atoms in the ``Input orientation`` table: that table prints six
#: decimals, the archive ten, so the rounding is 5e-7; this is loose enough for that and far tighter than any
#: different orientation.
_ORIENTATION_ATOL_ANGSTROM = 1e-5

_ARCHIVE_START = re.compile(r" 1\\1\\")
_FLOAT = re.compile(r"[+-]?\d+\.\d*(?:[eE][+-]?\d+)?")
_ELEMENT = re.compile(r"[A-Za-z]{1,2}")


@dataclass(frozen=True)
class ArchiveHessianRead:
    """``parsed`` when a trustworthy matrix was read; else ``reason`` says why none was (``None``: no archive Hessian)."""

    parsed: ParsedHessian | None = None
    reason: str | None = None


def _archive_blocks(text: str) -> list[str]:
    r"""Each archive block, rejoined into one string (the wrap is removed, the section delimiters kept).

    A block is a run of non-blank lines from the opening line to the one that completes the ``\@`` terminator.
    The terminator can itself be split across a line break (a line ending ``\\\`` then one holding `` @``), so it
    is looked for in the rejoined text.
    A run that ends (blank line or end of file) without it is a truncated block and is dropped.
    """
    lines = text.split("\n")
    blocks: list[str] = []
    for start, line in enumerate(lines):
        if not _ARCHIVE_START.match(line):
            continue
        joined = ""
        for row in lines[start:]:
            if not row.strip():
                break
            previous = len(joined)
            joined += row[1:] if row.startswith(" ") else row
            if "\\@" in joined[max(previous - 1, 0) :]:
                blocks.append(joined[: joined.index("\\@", max(previous - 1, 0))])
                break
    return blocks


def _read_atoms(section: str) -> list[tuple[str, float, float, float]] | None:
    """The ``(element, x, y, z)`` rows of the charge/multiplicity-plus-atoms section, in Angstrom."""
    fields = section.split("\\")
    if len(fields) < 2:
        return None
    atoms: list[tuple[str, float, float, float]] = []
    for field in fields[1:]:
        parts = field.split(",")
        if len(parts) not in (4, 5):  # element, [frozen/fragment flag,] x, y, z
            return None
        if not _ELEMENT.fullmatch(parts[0]):
            return None  # an atomic number or a label this module does not interpret
        try:
            x, y, z = (float(v) for v in parts[-3:])
        except ValueError:
            return None
        atoms.append((parts[0], x, y, z))
    return atoms or None


def _read_values(section: str) -> list[float] | None:
    tokens = section.split(",")
    if not all(_FLOAT.fullmatch(t) for t in tokens):
        return None
    return [float(t) for t in tokens]


def _last_orientation_table(text: str, title: str) -> list[tuple[int, float, float, float]] | None:
    """The last ``<title> orientation:`` table in the log as ``(atomic_number, x, y, z)`` rows."""
    lines = text.splitlines()
    found: list[tuple[int, float, float, float]] | None = None
    for i, line in enumerate(lines):
        if line.strip() != f"{title} orientation:":
            continue
        rows: list[tuple[int, float, float, float]] = []
        dashes = 0
        for row in lines[i + 1 :]:
            if row.strip().startswith("-----"):
                dashes += 1
                if dashes == 3:
                    break
                continue
            if dashes < 2:
                continue
            parts = row.split()
            if len(parts) != 6:
                rows = []
                break
            try:
                rows.append((int(parts[1]), float(parts[3]), float(parts[4]), float(parts[5])))
            except ValueError:
                rows = []
                break
        if rows:
            found = rows
    return found


def _atoms_match_table(
    atoms: list[tuple[str, float, float, float]], table: list[tuple[int, float, float, float]] | None
) -> bool:
    if table is None or len(table) != len(atoms):
        return False
    return all(
        abs(a[1] - t[1]) <= _ORIENTATION_ATOL_ANGSTROM
        and abs(a[2] - t[2]) <= _ORIENTATION_ATOL_ANGSTROM
        and abs(a[3] - t[3]) <= _ORIENTATION_ATOL_ANGSTROM
        for a, t in zip(atoms, table, strict=True)
    )


def read_gaussian_archive_hessian(text: str) -> ArchiveHessianRead:
    """Read the Hessian from the last archive block that has one; never return a matrix whose frame is unproven."""
    for block in reversed(_archive_blocks(text)):
        sections = block.split("\\\\")
        nimag = next((i for i, s in enumerate(sections) if "NImag=" in s), None)
        if nimag is None or nimag + 1 >= len(sections) or len(sections) < 5:
            continue
        hessian_section = sections[nimag + 1]
        if not hessian_section.strip() or hessian_section[0] not in "+-0123456789.":
            continue

        atoms = _read_atoms(sections[3])
        values = _read_values(hessian_section)
        if atoms is None or values is None:
            return ArchiveHessianRead(reason=REASON_ARCHIVE_UNREADABLE)
        n3 = 3 * len(atoms)
        if len(values) != n3 * (n3 + 1) // 2:
            return ArchiveHessianRead(reason=REASON_ARCHIVE_UNREADABLE)

        if not _atoms_match_table(atoms, _last_orientation_table(text, "Input")):
            return ArchiveHessianRead(reason=REASON_ARCHIVE_ORIENTATION)

        return ArchiveHessianRead(
            parsed=ParsedHessian(
                natoms=len(atoms),
                lower_triangle_hartree_bohr2=values,
                source=HessianSource.parsed_log_archive,
                reference_coords_angstrom=atoms,
            )
        )
    return ArchiveHessianRead()
