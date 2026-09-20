"""QCSchema driver-hessian ``return_result`` -> packed lower triangle (C-Q1).

QCSchema's Hessian ``return_result`` is the *full* symmetric 3N x 3N
matrix, flattened row-major (a flat list of length ``(3N)**2``, confirmed
against a real Psi4/qcengine hessian document -- see
``tests/fixtures/README.md``). TCKDB stores only the packed lower triangle
*including the diagonal*, row-major, in the exact order
``backend/app/services/hessian_parsing.py:417`` packs ORCA's full matrix:
``[matrix[row][col] for row in range(3N) for col in range(row + 1)]``. This
module reshapes and packs to that same order, and applies the same
symmetry check ORCA parsing does before trusting the result.
"""

from __future__ import annotations

import numpy as np

from .errors import E_HESSIAN_ASYMMETRIC, QCSchemaAdapterError

#: Same tolerance as the ORCA full-matrix symmetry check in
#: ``backend/app/services/hessian_parsing.py`` (``_SYMMETRY_ATOL``),
#: hartree/bohr**2.
SYMMETRY_ATOL = 1e-6


def reshape_full_matrix(flat, natoms: int) -> list[list[float]]:
    """Flat (or nested, or numpy) row-major ``(3N)**2`` values -> nested ``3N x 3N`` matrix."""
    flat = [float(v) for v in np.asarray(flat, dtype=float).reshape(-1)]
    dim = 3 * natoms
    expected = dim * dim
    if len(flat) != expected:
        raise QCSchemaAdapterError(
            "hessian_shape_invalid",
            f"driver-hessian return_result has {len(flat)} entries but a "
            f"{natoms}-atom Hessian must have exactly {expected} "
            f"(= (3N)**2 for N={natoms}) as a flattened full matrix.",
            natoms=natoms,
            actual_length=len(flat),
            expected_length=expected,
        )
    return [flat[row * dim : (row + 1) * dim] for row in range(dim)]


def pack_lower_triangle(flat: list[float], natoms: int) -> list[float]:
    """Full flattened Hessian -> packed lower triangle, symmetry-checked.

    :raises QCSchemaAdapterError: ``hessian_asymmetric`` if the matrix is
        not numerically symmetric within :data:`SYMMETRY_ATOL`.
    """
    matrix = reshape_full_matrix(flat, natoms)
    dim = 3 * natoms

    for r in range(dim):
        for c in range(r):
            if abs(matrix[r][c] - matrix[c][r]) > SYMMETRY_ATOL:
                raise QCSchemaAdapterError(
                    E_HESSIAN_ASYMMETRIC,
                    f"Hessian is not symmetric within {SYMMETRY_ATOL} "
                    f"hartree/bohr**2: matrix[{r}][{c}]={matrix[r][c]!r} "
                    f"vs matrix[{c}][{r}]={matrix[c][r]!r}.",
                    row=r,
                    col=c,
                )

    # Row-major lower triangle *including the diagonal*, exactly the order
    # hessian_parsing.py:417 packs: [M[r][c] for r in range(N) for c in
    # range(r + 1)].
    return [matrix[row][col] for row in range(dim) for col in range(row + 1)]


__all__ = ["SYMMETRY_ATOL", "reshape_full_matrix", "pack_lower_triangle"]
