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

Frame. The archive's atoms are written in the frame the Hessian is in; this was measured, not assumed, and the
reader does not rely on it. A Hessian computed in one frame and paired with atoms in another is a wrong matrix
that looks right, so the reader tests the pair for self-consistency: a Cartesian Hessian annihilates rigid
translations and (at a stationary point, to the accuracy of the residual gradient) infinitesimal rigid rotations of
the atoms it was computed at. ``max ||H v|| / (||v|| ||H||_F)`` over the three translations and three rotations
(rotations about the centroid, atoms in bohr) is at most 1.1e-4 on the 123 logs measured here and 6.4e-4 on the
reviewer's 64 non-NoSymm G03/G09/G16 logs, and at least 3.7e-3 when the atoms are rotated 5 degrees away from the
Hessian's frame (1.8e-2 for a standard-versus-input orientation pair). The bound is
:data:`FRAME_CONSISTENCY_RTOL` = 2e-3, three times the largest consistent value and below the 5 degree case. This
catches a frame mismatch of more than a few degrees; it cannot see a pure translation, which no Hessian depends on.
The archive atoms are returned as the matrix's reference frame, so the caller's check against the bound geometry
still decides whether the matrix applies to that geometry.

Atom labels. ``C(Iso=13)``, ``C(Fragment=1)`` and a frozen-atom flag are parsed (the element is the leading
letters; the label does not change the Hessian). An atomic-number element or a Z-matrix geometry is not
interpreted and is refused with ``archive_atoms_unsupported``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from app.db.models.common import HessianSource
from app.services.hessian_parsing import ParsedHessian

#: Reason tokens, the ``reason`` of a ``hessian_not_stored_unparseable`` warning.
REASON_ARCHIVE_UNREADABLE = "archive_block_unreadable"
REASON_ARCHIVE_FRAME = "archive_frame_inconsistent"
REASON_ARCHIVE_ATOMS = "archive_atoms_unsupported"

#: Bound on the rigid-motion residual of the archive Hessian against the archive atoms; see the module docstring.
FRAME_CONSISTENCY_RTOL = 2e-3

_BOHR_PER_ANGSTROM = 1.0 / 0.52917721067

_ARCHIVE_START = re.compile(r" 1\\1\\")
_FLOAT = re.compile(r"[+-]?\d+\.\d*(?:[eE][+-]?\d+)?")
_ATOM = re.compile(
    r"(?P<el>[A-Za-z]{1,2})(?:\([^)]*\))?,(?:-?\d+,)?(?P<x>[^,]+),(?P<y>[^,]+),(?P<z>[^,]+)"
)


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
    """The ``(element, x, y, z)`` rows of the charge/multiplicity-plus-atoms section, in Angstrom.

    ``None`` when a row is not in Cartesian form (a Z-matrix row, an atomic number as the element).
    """
    fields = section.split("\\")
    if len(fields) < 2:
        return None
    atoms: list[tuple[str, float, float, float]] = []
    for field in fields[1:]:
        match = _ATOM.fullmatch(field)
        if match is None or not all(_FLOAT.fullmatch(match[c]) for c in "xyz"):
            return None
        atoms.append((match["el"], float(match["x"]), float(match["y"]), float(match["z"])))
    return atoms or None


def _read_values(section: str) -> list[float] | None:
    tokens = section.split(",")
    if not all(_FLOAT.fullmatch(t) for t in tokens):
        return None
    return [float(t) for t in tokens]


def rigid_motion_residual(packed: list[float], atoms: list[tuple[str, float, float, float]]) -> float:
    """``max ||H v|| / (||v|| ||H||_F)`` over the 3 translations and 3 rotations of ``atoms`` (Angstrom)."""
    n3 = 3 * len(atoms)
    matrix = np.zeros((n3, n3))
    rows, cols = np.tril_indices(n3)
    matrix[rows, cols] = packed
    matrix = matrix + matrix.T - np.diag(np.diag(matrix))
    scale = np.linalg.norm(matrix)
    if scale == 0.0:
        return float("inf")
    xyz = np.array([[x, y, z] for _, x, y, z in atoms]) * _BOHR_PER_ANGSTROM
    xyz = xyz - xyz.mean(axis=0)
    worst = 0.0
    for axis in range(3):
        unit = np.zeros(3)
        unit[axis] = 1.0
        for vector in (np.tile(unit, len(atoms)), np.cross(unit, xyz).reshape(-1)):
            norm = np.linalg.norm(vector)
            if norm > 0.0:
                worst = max(worst, float(np.linalg.norm(matrix @ vector) / (norm * scale)))
    return worst


def read_gaussian_archive_hessian(text: str) -> ArchiveHessianRead:
    """Read the Hessian from the last archive block that has an ``NImag=`` section.

    Once that block is found it decides: an unreadable Hessian in it is a refusal, never a reason to read an
    earlier block's matrix (which belongs to a different job step).
    """
    text = "\n".join(text.splitlines())  # one line ending, whatever the log was written with
    for block in reversed(_archive_blocks(text)):
        sections = block.split("\\\\")
        nimag = next((i for i, s in enumerate(sections) if "NImag=" in s), None)
        if nimag is None:
            continue
        if nimag + 1 >= len(sections) or not sections[nimag + 1].strip() or len(sections) < 5:
            return ArchiveHessianRead()  # a freq step that printed no matrix

        atoms = _read_atoms(sections[3])
        if atoms is None:
            return ArchiveHessianRead(reason=REASON_ARCHIVE_ATOMS)
        values = _read_values(sections[nimag + 1])
        n3 = 3 * len(atoms)
        if values is None or len(values) != n3 * (n3 + 1) // 2:
            return ArchiveHessianRead(reason=REASON_ARCHIVE_UNREADABLE)
        if rigid_motion_residual(values, atoms) > FRAME_CONSISTENCY_RTOL:
            return ArchiveHessianRead(reason=REASON_ARCHIVE_FRAME)

        return ArchiveHessianRead(
            parsed=ParsedHessian(
                natoms=len(atoms),
                lower_triangle_hartree_bohr2=values,
                source=HessianSource.parsed_log_archive,
                reference_coords_angstrom=atoms,
            )
        )
    return ArchiveHessianRead()
