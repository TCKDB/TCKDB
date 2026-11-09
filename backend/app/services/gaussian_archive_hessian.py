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
that looks right, so the reader tests the pair for self-consistency. Rotation invariance of the energy gives, for
an infinitesimal rotation ``v = e x r`` of the atoms, ``H v = e x g`` (``g`` the gradient), and ``H t = 0`` for a
translation ``t``. The test is the worst ``||H v - target|| / (||v|| ||H||_F / sqrt(3N))`` over the six motions
(rotations about the centroid, atoms in bohr), with the archive's own gradient (the section after the Hessian)
in ``target``, so a frequency job at a non-stationary geometry is not mistaken for a frame mismatch; without a
gradient section the target is zero. Dividing by the rms row norm ``||H||_F / sqrt(3N)`` keeps the sensitivity as
the molecule grows.

Measured: on the 123 E2 and G09 logs here the largest consistent value is 6.4e-4, and atoms rotated 2 degrees
away from the matrix's frame give at least 1.0e-2. On the reviewer's 188 logs (up to 53 atoms) the largest
consistent value is 6.42e-4 and the smallest 2 degree value 4.52e-3. The bound is :data:`FRAME_CONSISTENCY_RTOL` =
2e-3: three times the largest consistent value measured anywhere and well below the smallest 2 degree value. It
catches a frame mismatch of 2 degrees or more; a smaller one, and a pure translation (which no Hessian depends
on), pass. It is not a test of *which geometry* the matrix was computed at: a G3-family frequency step (HF/6-31G(d))
runs at a different geometry from the MP2(Full) one the final archive block carries, and that pair measured 1.8e-3.
That case is refused structurally, below.

Composite jobs. A composite job's final ``Mixed`` archive block holds the final geometry, and its ``NImag=`` Hessian
comes from the frequency step. G1, G2, G2MP2, G3, G3MP2 and CBS-APNO run the frequency step (HF) at a different
geometry from the final one (MP2 or QCISD); CBS-QB3, ROCBS-QB3, CBS-4M, G3B3, G3MP2B3, G4, G4MP2 and the W1 family run
both at one geometry. Rather than list methods, the reader compares the ``Mixed`` block's atoms with those of the
earlier ``Freq`` block and refuses (``archive_composite_geometry_mismatch``) when they differ by more than
:data:`COMPOSITE_GEOMETRY_ATOL_ANGSTROM`. Checked on the fixtures: G3 differs; CBS-QB3 (G03, G09, G16), ROCBS-QB3,
CBS-4M, G4 and G4MP2 agree.

The archive atoms are returned as the matrix's reference frame, so the
caller's check against the bound geometry still decides whether the matrix applies to that geometry.

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
REASON_ARCHIVE_COMPOSITE = "archive_composite_geometry_mismatch"

#: Atoms of a composite job's final block and of its ``Freq`` block coincide to the archive's ten decimals when the
#: two steps ran at one geometry; this allows for nothing but rounding. The G3 pair differs by ~1e-2 Angstrom.
COMPOSITE_GEOMETRY_ATOL_ANGSTROM = 1e-4

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


def rigid_motion_residual(
    packed: list[float],
    atoms: list[tuple[str, float, float, float]],
    gradient: list[float] | None = None,
) -> float:
    """Worst ``||H v - t|| / (||v|| ||H||_F / sqrt(3N))`` over the rigid motions ``v`` of ``atoms`` (Angstrom).

    The motions are the 3 translations (``t = 0``) and the 3 rotations about the centroid, ``v_i = e x r_i`` with
    ``r`` in bohr. Rotation invariance of the energy gives ``H v = e x g`` (``g`` the gradient, per atom), which
    is zero only at a stationary point; a frequency job at a non-stationary geometry has ``g != 0``. With the
    archive's own ``gradient`` (``dE/dx``, hartree/bohr: it equals minus the log's ``Forces`` table; checked on
    two logs) the target is ``e x g``; without it the target is zero. ``||H||_F / sqrt(3N)`` is the rms row norm,
    so the residual keeps its sensitivity as the molecule grows (``||H||_F`` alone grows with ``sqrt(3N)``). A
    rotation whose ``v`` is under 1% of the largest rotation's (the axis of a linear molecule) is skipped: it
    carries no information and dividing by it only amplifies rounding.
    """
    n3 = 3 * len(atoms)
    matrix = np.zeros((n3, n3))
    rows, cols = np.tril_indices(n3)
    matrix[rows, cols] = packed
    matrix = matrix + matrix.T - np.diag(np.diag(matrix))
    scale = np.linalg.norm(matrix) / np.sqrt(n3)
    if scale == 0.0:
        return float("inf")
    xyz = np.array([[x, y, z] for _, x, y, z in atoms]) * _BOHR_PER_ANGSTROM
    xyz = xyz - xyz.mean(axis=0)
    grad = None if gradient is None else np.asarray(gradient, dtype=float).reshape(-1, 3)
    units = np.eye(3)
    rotations = [np.cross(unit, xyz).reshape(-1) for unit in units]
    largest = max(np.linalg.norm(v) for v in rotations)
    worst = 0.0
    for axis, unit in enumerate(units):
        translation = np.tile(unit, len(atoms))
        worst = max(worst, float(np.linalg.norm(matrix @ translation) / (np.linalg.norm(translation) * scale)))
        vector = rotations[axis]
        norm = np.linalg.norm(vector)
        if norm < 0.01 * largest:
            continue
        target = np.zeros(n3) if grad is None else np.cross(unit, grad).reshape(-1)
        worst = max(worst, float(np.linalg.norm(matrix @ vector - target) / (norm * scale)))
    return worst


def _job_type(block: str) -> str | None:
    header = block.split("\\\\", 1)[0].split("\\")
    return header[3] if len(header) > 3 else None


def _composite_geometry_differs(blocks: list[str], atoms: list[tuple[str, float, float, float]]) -> bool:
    """True when ``blocks`` hold a ``Freq`` step whose atoms differ from the final block's ``atoms``."""
    for earlier in reversed(blocks[:-1]):
        if _job_type(earlier) != "Freq":
            continue
        freq_sections = earlier.split("\\\\")
        freq_atoms = _read_atoms(freq_sections[3]) if len(freq_sections) > 3 else None
        if freq_atoms is None:
            return False
        if len(freq_atoms) != len(atoms):
            return True
        return any(
            abs(a - b) > COMPOSITE_GEOMETRY_ATOL_ANGSTROM
            for (_, *p), (_, *q) in zip(atoms, freq_atoms, strict=True)
            for a, b in zip(p, q, strict=True)
        )
    return False


def read_gaussian_archive_hessian(text: str) -> ArchiveHessianRead:
    """Read the Hessian from the last archive block that has an ``NImag=`` section.

    Once that block is found it decides: an unreadable Hessian in it is a refusal, never a reason to read an
    earlier block's matrix (which belongs to a different job step).
    """
    text = "\n".join(text.splitlines())  # one line ending, whatever the log was written with
    blocks = _archive_blocks(text)
    for position in range(len(blocks) - 1, -1, -1):
        block = blocks[position]
        sections = block.split("\\\\")
        nimag = next((i for i, s in enumerate(sections) if "NImag=" in s), None)
        if nimag is None:
            continue
        if nimag + 1 >= len(sections) or not sections[nimag + 1].strip() or len(sections) < 5:
            return ArchiveHessianRead()  # a freq step that printed no matrix

        atoms = _read_atoms(sections[3])
        if atoms is None:
            return ArchiveHessianRead(reason=REASON_ARCHIVE_ATOMS)
        if _job_type(block) == "Mixed" and _composite_geometry_differs(blocks[: position + 1], atoms):
            return ArchiveHessianRead(reason=REASON_ARCHIVE_COMPOSITE)
        values = _read_values(sections[nimag + 1])
        n3 = 3 * len(atoms)
        if values is None or len(values) != n3 * (n3 + 1) // 2:
            return ArchiveHessianRead(reason=REASON_ARCHIVE_UNREADABLE)
        gradient = _read_values(sections[nimag + 2]) if nimag + 2 < len(sections) else None
        if gradient is not None and len(gradient) != n3:
            gradient = None  # not the gradient section: fall back to the stationary-point test
        if rigid_motion_residual(values, atoms, gradient) > FRAME_CONSISTENCY_RTOL:
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
