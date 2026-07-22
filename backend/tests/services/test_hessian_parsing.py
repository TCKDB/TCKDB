"""DB-free tests for Cartesian Hessian parsing from ESS artifacts.

Exercises :mod:`app.services.hessian_parsing` and the per-program
``parse_hessian`` entry points against real fixtures:

* Gaussian output log (``Force constants in Cartesian coordinates:``)
* Molpro output log (``Force Constants ... in [a.u.]``)
* ORCA ``.hess`` file (``$hessian`` block)

The central invariant: TCKDB keeps the program's **native** hartree/bohr²
values verbatim (no J/m² conversion). Each test pins the first raw fixture
value straight through to the packed lower triangle, and checks the packed
length, ``natoms``, symmetry, and the reported source.
"""

from __future__ import annotations

import math
from pathlib import Path

from app.db.models.common import HessianSource
from app.services.gaussian_parameter_parser import parse_hessian as parse_gaussian
from app.services.hessian_parsing import (
    BOHR_TO_ANGSTROM,
    GAUSSIAN_HESSIAN_MARKER,
    HESSIAN_PARSER_VERSION,
    parse_hessian_from_artifact,
    parse_orca_hess_force_constants,
    parse_orca_hess_reference_atoms,
    parse_triangular_force_constants,
)
from app.services.molpro_parameter_parser import parse_hessian as parse_molpro
from app.services.orca_parameter_parser import parse_hessian as parse_orca

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

GAUSSIAN_LOG = (FIXTURES / "gaussian" / "freq_g09.log").read_text()
MOLPRO_LOG = (FIXTURES / "molpro" / "molpro_TS_freq.out").read_text()
ORCA_HESS = (FIXTURES / "orca" / "Orca_TS_test.hess").read_text()


def _triangle_len(natoms: int) -> int:
    n3 = 3 * natoms
    return n3 * (n3 + 1) // 2


def _unpack(natoms: int, triangle: list[float]) -> list[list[float]]:
    n3 = 3 * natoms
    matrix = [[0.0] * n3 for _ in range(n3)]
    idx = 0
    for r in range(n3):
        for c in range(r + 1):
            matrix[r][c] = matrix[c][r] = triangle[idx]
            idx += 1
    return matrix


class TestGaussianHessian:
    def test_parses_native_units_and_shape(self):
        result = parse_gaussian(GAUSSIAN_LOG)
        assert result is not None
        natoms, triangle = result
        assert natoms == 12
        assert len(triangle) == _triangle_len(natoms) == 666

    def test_first_value_is_verbatim_atomic_units(self):
        # Fixture line ``1  0.410282D-01`` -> the (0,0) diagonal entry. Stored
        # as-is in hartree/bohr²; NOT multiplied by Arkane's J/m² factor.
        _, triangle = parse_gaussian(GAUSSIAN_LOG)
        assert triangle[0] == 0.410282e-1
        # ``2 -0.152370D-01  0.432137D+00`` -> (1,0) then (1,1).
        assert triangle[1] == -0.152370e-1
        assert triangle[2] == 0.432137e0

    def test_matrix_is_symmetric(self):
        natoms, triangle = parse_gaussian(GAUSSIAN_LOG)
        matrix = _unpack(natoms, triangle)
        n3 = 3 * natoms
        for r in range(n3):
            for c in range(n3):
                assert matrix[r][c] == matrix[c][r]


class TestMolproHessian:
    def test_parses_native_units_and_shape(self):
        result = parse_molpro(MOLPRO_LOG)
        assert result is not None
        natoms, triangle = result
        assert natoms == 5
        assert len(triangle) == _triangle_len(natoms) == 120

    def test_first_value_is_verbatim_atomic_units(self):
        # Fixture ``OX1  0.3700857`` -> (0,0). Molpro already prints [a.u.].
        _, triangle = parse_molpro(MOLPRO_LOG)
        assert triangle[0] == 0.3700857
        # ``OY1  -0.0000614   0.0485462`` -> (1,0), (1,1).
        assert triangle[1] == -0.0000614
        assert triangle[2] == 0.0485462

    def test_offdiagonal_symmetric_entry(self):
        # ``CX2  -0.3483365 ... -0.1579239`` -> (3,2) column-3 row.
        natoms, triangle = parse_molpro(MOLPRO_LOG)
        matrix = _unpack(natoms, triangle)
        assert matrix[3][2] == -0.1579239
        assert matrix[2][3] == -0.1579239


class TestOrcaHessian:
    def test_parses_native_units_and_shape(self):
        result = parse_orca(ORCA_HESS)
        assert result is not None
        natoms, triangle = result
        assert natoms == 6  # $hessian dimension 18 = 3N
        assert len(triangle) == _triangle_len(natoms) == 171

    def test_first_value_is_verbatim_atomic_units(self):
        # ``.hess`` row 0 col 0 = ``-6.9820446273E-02``; kept, not converted.
        _, triangle = parse_orca(ORCA_HESS)
        assert triangle[0] == -6.9820446273e-2
        assert triangle[2] == 5.7626450938e-1  # (1,1)


class TestArtifactDispatch:
    def test_gaussian_log_dispatched_by_banner(self):
        parsed = parse_hessian_from_artifact(GAUSSIAN_LOG, from_hess_file=False)
        assert parsed is not None
        assert parsed.source is HessianSource.parsed_log
        assert parsed.natoms == 12
        assert parsed.lower_triangle_hartree_bohr2[0] == 0.410282e-1

    def test_molpro_log_dispatched_by_banner(self):
        parsed = parse_hessian_from_artifact(MOLPRO_LOG, from_hess_file=False)
        assert parsed is not None
        assert parsed.source is HessianSource.parsed_log
        assert parsed.natoms == 5

    def test_orca_hess_dispatched_by_kind(self):
        # A .hess has no program banner -> dispatched by artifact kind.
        parsed = parse_hessian_from_artifact(ORCA_HESS, from_hess_file=True)
        assert parsed is not None
        assert parsed.source is HessianSource.parsed_hess
        assert parsed.natoms == 6

    def test_orca_hess_via_log_path_returns_none(self):
        # Content-sniffing a .hess as an output log finds no banner.
        assert parse_hessian_from_artifact(ORCA_HESS, from_hess_file=False) is None

    def test_unknown_banner_returns_none(self):
        assert parse_hessian_from_artifact("random text\n", from_hess_file=False) is None

    def test_empty_or_none_returns_none(self):
        assert parse_hessian_from_artifact("", from_hess_file=False) is None
        assert parse_hessian_from_artifact(None, from_hess_file=True) is None

    def test_parser_version_is_stamped_constant(self):
        assert HESSIAN_PARSER_VERSION == "hessian_v1"


class TestRobustness:
    def test_last_matrix_wins_when_repeated(self):
        # Two Gaussian-style blocks for a 1-atom (3x3) matrix; the second
        # must win (mirrors Gaussian re-printing the FC matrix in opt+freq).
        block_a = (
            f" {GAUSSIAN_HESSIAN_MARKER}\n"
            "                1             2             3\n"
            "      1  1.0\n"
            "      2  2.0  3.0\n"
            "      3  4.0  5.0  6.0\n"
            "\n"
        )
        block_b = (
            f" {GAUSSIAN_HESSIAN_MARKER}\n"
            "                1             2             3\n"
            "      1  10.0\n"
            "      2  20.0  30.0\n"
            "      3  40.0  50.0  60.0\n"
            "\n"
        )
        natoms, triangle = parse_triangular_force_constants(
            block_a + block_b, marker=GAUSSIAN_HESSIAN_MARKER
        )
        assert natoms == 1
        assert triangle == [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]

    def test_truncated_block_returns_none(self):
        # Dimension implied by first block is 3, but rows are missing.
        truncated = (
            f" {GAUSSIAN_HESSIAN_MARKER}\n"
            "                1             2             3\n"
            "      1  1.0\n"
            "      2  2.0  3.0\n"
        )
        assert (
            parse_triangular_force_constants(
                truncated, marker=GAUSSIAN_HESSIAN_MARKER
            )
            is None
        )

    def test_non_multiple_of_three_returns_none(self):
        # A 2x2 first block (not divisible by 3) is rejected.
        bad = (
            f" {GAUSSIAN_HESSIAN_MARKER}\n"
            "                1             2\n"
            "      1  1.0\n"
            "      2  2.0  3.0\n"
            "\n"
        )
        assert (
            parse_triangular_force_constants(bad, marker=GAUSSIAN_HESSIAN_MARKER)
            is None
        )

    def test_missing_marker_returns_none(self):
        assert (
            parse_triangular_force_constants(
                "no matrix here", marker=GAUSSIAN_HESSIAN_MARKER
            )
            is None
        )


# ---------------------------------------------------------------------------
# Synthetic builders for the guard tests
# ---------------------------------------------------------------------------


def _fmt_d(value: float) -> str:
    """Format a value in Gaussian ``D``-exponent notation (``1.000000D-02``)."""
    return f"{value:.6E}".replace("E", "D")


def _gaussian_fc(natoms: int, *, route: str | None = None, iop: bool = False) -> str:
    """A synthetic Gaussian ``Force constants in Cartesian coordinates`` block."""
    n3 = 3 * natoms
    out: list[str] = []
    if route is not None:
        out.append(f" #p {route}")
        out.append("")
    if iop:
        out.append(" IOp(2/9=2000) requested")
    out.append(f" {GAUSSIAN_HESSIAN_MARKER} ")
    for b in range(math.ceil(n3 / 5.0)):
        cols = range(b * 5, min(b * 5 + 5, n3))
        out.append("      " + "".join(f"{c + 1:>14}" for c in cols))
        for r in range(b * 5, n3):
            vals = [_fmt_d(0.01 * (min(r, c) + 1)) for c in cols if c <= r]
            out.append(f"{r + 1:>7} " + " ".join(vals))
    out.append("")  # blank terminator
    return "\n".join(out)


# A minimal valid ORCA .hess (natoms=1, 3x3, symmetric, complete markers).
_ORCA_BASE = """$orca_hessian_file

$act_atom
  0

$act_coord
  2

$hessian
3
                    0                  1                  2
    0     1.0000000000E+00   1.0000000000E-01   2.0000000000E-01
    1     1.0000000000E-01   2.0000000000E+00   3.0000000000E-01
    2     2.0000000000E-01   3.0000000000E-01   3.0000000000E+00

$atoms
1
 C     12.01100      0.100000000000    0.200000000000    0.300000000000

$end
"""


class TestStrictFloatTruncation:
    def test_gaussian_truncated_final_token_rejects_block(self):
        # A killed job leaves a final token missing its exponent digits; it must
        # NOT be read as 0.300000 (orders of magnitude off) — reject the block.
        valid = (
            f" {GAUSSIAN_HESSIAN_MARKER} \n"
            "                1             2             3\n"
            "      1  0.100000D+00\n"
            "      2  0.100000D-01  0.200000D+00\n"
            "      3  0.200000D-01  0.300000D-01  0.300000D+00\n"
            "\n"
        )
        assert parse_triangular_force_constants(
            valid, marker=GAUSSIAN_HESSIAN_MARKER
        ) == (1, [0.1, 0.01, 0.2, 0.02, 0.03, 0.3])

        truncated = valid.replace("0.300000D+00\n", "0.300000D+\n")
        assert (
            parse_triangular_force_constants(truncated, marker=GAUSSIAN_HESSIAN_MARKER)
            is None
        )


class TestOrcaGuards:
    def test_base_fixture_parses(self):
        assert parse_orca_hess_force_constants(_ORCA_BASE) == (
            1,
            [1.0, 0.1, 2.0, 0.2, 0.3, 3.0],
        )

    def test_row_missing_a_value_rejected(self):
        broken = _ORCA_BASE.replace(
            "    2     2.0000000000E-01   3.0000000000E-01   3.0000000000E+00",
            "    2     2.0000000000E-01   3.0000000000E-01",
        )
        assert parse_orca_hess_force_constants(broken) is None

    def test_corrupted_row_token_rejected(self):
        broken = _ORCA_BASE.replace(
            "    1     1.0000000000E-01   2.0000000000E+00   3.0000000000E-01",
            "    1     1.0000000000E-01   ***   3.0000000000E-01",
        )
        assert parse_orca_hess_force_constants(broken) is None

    def test_lying_dimension_rejected(self):
        # Dimension claims 6 but only a 3x3 block is present -> the reader runs
        # into $atoms and the per-row count fails.
        broken = _ORCA_BASE.replace("$hessian\n3\n", "$hessian\n6\n")
        assert parse_orca_hess_force_constants(broken) is None

    def test_asymmetric_matrix_rejected(self):
        broken = _ORCA_BASE.replace(
            "    1     1.0000000000E-01   2.0000000000E+00   3.0000000000E-01",
            "    1     9.0000000000E-01   2.0000000000E+00   3.0000000000E-01",
        )
        assert parse_orca_hess_force_constants(broken) is None

    def test_incomplete_act_marker_rejected(self):
        # Last displacement recorded is coord 1 (y), not the final coord 2 (z).
        broken = _ORCA_BASE.replace("$act_coord\n  2\n", "$act_coord\n  1\n")
        assert parse_orca_hess_force_constants(broken) is None

    def test_missing_atoms_block_still_parses_but_no_reference(self):
        no_atoms = _ORCA_BASE.split("$atoms")[0] + "$end\n"
        assert parse_orca_hess_force_constants(no_atoms) == (
            1,
            [1.0, 0.1, 2.0, 0.2, 0.3, 3.0],
        )
        parsed = parse_hessian_from_artifact(no_atoms, from_hess_file=True)
        assert parsed is not None
        assert parsed.reference_coords_angstrom is None


class TestOrcaReferenceAtoms:
    def test_parses_real_fixture_atoms_in_angstrom(self):
        atoms = parse_orca_hess_reference_atoms(ORCA_HESS)
        assert atoms is not None
        assert len(atoms) == 6
        element, x, y, z = atoms[0]
        assert element == "C"
        # First $atoms row: C ... -3.714532101532 (bohr) -> Angstrom.
        assert x == -3.714532101532 * BOHR_TO_ANGSTROM
        assert [a[0] for a in atoms] == ["C", "Cl", "H", "H", "H", "F"]

    def test_dispatch_attaches_reference_coords(self):
        parsed = parse_hessian_from_artifact(ORCA_HESS, from_hess_file=True)
        assert parsed is not None
        assert parsed.reference_coords_angstrom is not None
        assert len(parsed.reference_coords_angstrom) == 6


class TestGaussianOrientationGuard:
    def test_opt_over_13_atoms_without_iop_is_skipped(self):
        # Arkane's standard-orientation trap: opt + >13 atoms + no IOp(2/9=2000)
        # -> the printed FC matrix is not in input-orientation Cartesians.
        text = _gaussian_fc(14, route="opt freq")
        # The raw matrix parses fine...
        assert parse_triangular_force_constants(
            text, marker=GAUSSIAN_HESSIAN_MARKER
        ) is not None
        # ...but the orientation guard skips it.
        assert parse_gaussian(text) is None

    def test_opt_over_13_atoms_with_iop_is_kept(self):
        text = _gaussian_fc(14, route="opt freq", iop=True)
        result = parse_gaussian(text)
        assert result is not None
        assert result[0] == 14

    def test_freq_only_over_13_atoms_is_kept(self):
        # A freq job with no opt keeps the input orientation.
        text = _gaussian_fc(14, route="freq")
        result = parse_gaussian(text)
        assert result is not None
        assert result[0] == 14

    def test_opt_under_14_atoms_is_kept(self):
        text = _gaussian_fc(12, route="opt freq")
        result = parse_gaussian(text)
        assert result is not None
        assert result[0] == 12
