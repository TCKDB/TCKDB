"""Read the Cartesian Hessian from a Gaussian log's archive block (logs with no force-constant block)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from app.db.models.common import HessianSource
from app.services.hessian_parsing import ParsedHessian

#: Reasons of a ``hessian_not_stored_unparseable`` warning.
REASON_ARCHIVE_UNREADABLE = "archive_block_unreadable"
REASON_ARCHIVE_FRAME = "archive_frame_inconsistent"
REASON_ARCHIVE_ATOMS = "archive_atoms_unsupported"
REASON_ARCHIVE_COMPOSITE = "archive_composite_geometry_mismatch"

#: Largest difference (Angstrom) between a composite job's final block and its ``Freq`` block atoms.
COMPOSITE_GEOMETRY_ATOL_ANGSTROM = 1e-4

#: Largest rigid-motion residual accepted by :func:`rigid_motion_residual`.
FRAME_CONSISTENCY_RTOL = 2e-3

_BOHR_PER_ANGSTROM = 1.0 / 0.52917721067

_ARCHIVE_START = re.compile(r" 1\\1\\")
_FLOAT = re.compile(r"[+-]?\d+\.\d*(?:[eE][+-]?\d+)?")
_ATOM = re.compile(
    r"(?P<el>[A-Za-z]{1,2})(?:\([^)]*\))?,(?:-?\d+,)?(?P<x>[^,]+),(?P<y>[^,]+),(?P<z>[^,]+)"
)


@dataclass(frozen=True)
class ArchiveHessianRead:
    """The matrix read, or the reason none was (both ``None``: no archive Hessian)."""

    parsed: ParsedHessian | None = None
    reason: str | None = None


def _archive_blocks(text: str) -> list[str]:
    """Each terminated archive block, rejoined across its line wraps."""
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
    """The ``(element, x, y, z)`` Cartesian rows in Angstrom, or ``None`` for any other atom form."""
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
    """Worst ``||H v - t|| / (||v|| ||H||_F / sqrt(3N))`` over rigid translations and rotations of ``atoms``."""
    # Rotation invariance gives H (e x r) = e x g (zero at a stationary point); the archive gradient is dE/dx.
    # A rotation about the axis of a linear molecule (v under 1% of the largest) carries no information.
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
    """Read the Hessian from the last archive block with an ``NImag=`` section; an unreadable one is a refusal."""
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
