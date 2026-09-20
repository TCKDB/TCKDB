"""Unit tests for the Hessian packing order and symmetry check (C-Q1).

These pin exact numeric values, not just "did it validate" -- the corpus
test's length-only check would pass an upper-triangle-packed matrix just
as happily as a lower-triangle one (both have ``3N(3N+1)/2`` entries), so
catching a swapped packing order needs a known-value comparison, done
here directly against :func:`pack_lower_triangle` rather than through the
whole mapping pipeline.
"""

from __future__ import annotations

import pytest

from tckdb_qcschema.errors import QCSchemaAdapterError
from tckdb_qcschema.hessian import pack_lower_triangle, reshape_full_matrix

#: A deliberately asymmetric-looking-if-mis-packed 2-atom (3N=6) matrix,
#: every entry distinct so a transposition or a wrong traversal order is
#: visible in the packed output. Genuinely symmetric (M[r][c] == M[c][r]).
_N = 2
_DIM = 3 * _N


def _make_symmetric_matrix() -> list[list[float]]:
    matrix = [[0.0] * _DIM for _ in range(_DIM)]
    value = 1
    for r in range(_DIM):
        for c in range(r, _DIM):
            matrix[r][c] = matrix[c][r] = float(value)
            value += 1
    return matrix


def _flatten_row_major(matrix: list[list[float]]) -> list[float]:
    return [matrix[r][c] for r in range(_DIM) for c in range(_DIM)]


def test_pack_lower_triangle_matches_exact_row_major_lower_order():
    matrix = _make_symmetric_matrix()
    flat_full = _flatten_row_major(matrix)

    packed = pack_lower_triangle(flat_full, _N)

    expected = [matrix[row][col] for row in range(_DIM) for col in range(row + 1)]
    assert packed == expected
    assert len(packed) == _DIM * (_DIM + 1) // 2

    # Specifically NOT the upper-triangle order (different traversal ->
    # different sequence for this all-distinct-value matrix).
    upper_order = [matrix[row][col] for row in range(_DIM) for col in range(row, _DIM)]
    assert packed != upper_order


def test_reshape_full_matrix_is_row_major():
    matrix = _make_symmetric_matrix()
    flat_full = _flatten_row_major(matrix)
    reshaped = reshape_full_matrix(flat_full, _N)
    assert reshaped == matrix


def test_asymmetric_matrix_refuses_hessian_asymmetric():
    matrix = _make_symmetric_matrix()
    matrix[0][1] += 10.0  # break symmetry beyond tolerance; matrix[1][0] untouched
    flat_full = _flatten_row_major(matrix)
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        pack_lower_triangle(flat_full, _N)
    assert excinfo.value.code == "hessian_asymmetric"


def test_wrong_length_refuses_hessian_shape_invalid():
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        pack_lower_triangle([1.0, 2.0, 3.0], _N)
    assert excinfo.value.code == "hessian_shape_invalid"
