"""DB-free tests for starting-geometry parsing from ESS artifacts.

Exercises :mod:`app.services.input_geometry_extraction`'s pure parse
functions against real and small hand-authored fixtures:

* Gaussian input deck (Cartesian ``.gjf``) and its Z-matrix rejection
* Gaussian output log's first "Input orientation" block
  (``tests/fixtures/gaussian/opt_g09.log``, a real ARC-produced log)
* ORCA input deck's inline ``* xyz`` block, and its ``* xyzfile`` /
  ``* int`` rejections
* ORCA output log's first "CARTESIAN COORDINATES (ANGSTROEM)" block
  (``tests/fixtures/orca/opt_orca.out``, a real run whose *input* actually
  used ``* xyzfile`` -- proving the log fallback does not depend on how
  the input deck declared its coordinates)

DB-aware behaviour (eligibility, minting/dedup, linking) is covered at the
API level in ``tests/api/test_api_artifact_input_geometry_hook.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models.common import ArtifactKind
from app.services.input_geometry_extraction import (
    InputGeometryParseAction,
    extract_input_geometry,
    parse_gaussian_input_geometry,
    parse_gaussian_log_input_geometry,
    parse_orca_input_geometry,
    parse_orca_log_input_geometry,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
INPUT_GEOMETRY_FIXTURES = FIXTURES / "input_geometry"

GAUSSIAN_GJF = (INPUT_GEOMETRY_FIXTURES / "gaussian_opt_input.gjf").read_text()
GAUSSIAN_GJF_ZMATRIX = (
    INPUT_GEOMETRY_FIXTURES / "gaussian_opt_input_zmatrix.gjf"
).read_text()
ORCA_INP = (INPUT_GEOMETRY_FIXTURES / "orca_opt_input.inp").read_text()
ORCA_INP_XYZFILE = (
    INPUT_GEOMETRY_FIXTURES / "orca_opt_input_xyzfile.inp"
).read_text()
ORCA_INP_INTERNAL = (
    INPUT_GEOMETRY_FIXTURES / "orca_opt_input_internal.inp"
).read_text()
ORCA_LOG_HEAD = (INPUT_GEOMETRY_FIXTURES / "orca_opt_output_head.out").read_text()

GAUSSIAN_OPT_LOG = (FIXTURES / "gaussian" / "opt_g09.log").read_text()  # 12 atoms
ORCA_OPT_LOG = (FIXTURES / "orca" / "opt_orca.out").read_text()  # 18 atoms


class TestGaussianInputParsing:
    def test_cartesian_deck_extracts_three_atoms(self):
        result = parse_gaussian_input_geometry(GAUSSIAN_GJF)
        assert result.action is InputGeometryParseAction.extracted
        assert result.origin == "gaussian_input"
        assert [a[0] for a in result.atoms] == ["O", "H", "H"]
        # First atom's coordinates survive verbatim.
        assert result.atoms[0][1:] == pytest.approx((0.0, 0.0, 0.118351))

    def test_zmatrix_deck_is_not_determinable(self):
        result = parse_gaussian_input_geometry(GAUSSIAN_GJF_ZMATRIX)
        assert result.action is InputGeometryParseAction.not_determinable
        assert result.atoms is None
        assert result.reason is not None and "Z-matrix" in result.reason

    def test_empty_text_is_not_determinable(self):
        result = parse_gaussian_input_geometry("")
        assert result.action is InputGeometryParseAction.not_determinable

    def test_no_route_line_is_not_determinable(self):
        result = parse_gaussian_input_geometry("just some text\nwith no route\n")
        assert result.action is InputGeometryParseAction.not_determinable
        assert "route" in (result.reason or "")


class TestGaussianLogParsing:
    def test_first_geometry_block_extracts_twelve_atoms(self):
        result = parse_gaussian_log_input_geometry(GAUSSIAN_OPT_LOG)
        assert result.action is InputGeometryParseAction.extracted
        assert result.origin == "gaussian_log"
        assert len(result.atoms) == 12
        # First atom of the *first* "Input orientation" block (real fixture,
        # pinned so a regression in "first vs last" is caught immediately).
        first_element, x, y, z = result.atoms[0]
        assert first_element == "N"
        assert (x, y, z) == pytest.approx((1.500826, 1.178575, 1.184192))

    def test_no_geometry_block_is_not_determinable(self):
        result = parse_gaussian_log_input_geometry("Entering Gaussian System\nnothing here\n")
        assert result.action is InputGeometryParseAction.not_determinable


class TestOrcaInputParsing:
    def test_inline_xyz_block_extracts_three_atoms(self):
        result = parse_orca_input_geometry(ORCA_INP)
        assert result.action is InputGeometryParseAction.extracted
        assert result.origin == "orca_input"
        assert [a[0] for a in result.atoms] == ["O", "H", "H"]
        assert result.atoms[0][1:] == pytest.approx((0.0, 0.0, 0.118351))

    def test_xyzfile_directive_is_not_determinable(self):
        result = parse_orca_input_geometry(ORCA_INP_XYZFILE)
        assert result.action is InputGeometryParseAction.not_determinable
        assert "xyzfile" in (result.reason or "")

    def test_internal_coordinate_directive_is_not_determinable(self):
        result = parse_orca_input_geometry(ORCA_INP_INTERNAL)
        assert result.action is InputGeometryParseAction.not_determinable
        assert "int" in (result.reason or "")

    def test_no_directive_is_not_determinable(self):
        result = parse_orca_input_geometry("! B3LYP def2-TZVP Opt\n")
        assert result.action is InputGeometryParseAction.not_determinable


class TestOrcaLogParsing:
    def test_first_block_extracts_three_atoms_from_hand_authored_head(self):
        # This fixture's own INPUT FILE echo uses '* xyzfile' -- proving the
        # log fallback recovers a geometry even when the input-file path
        # would have rejected it.
        result = parse_orca_log_input_geometry(ORCA_LOG_HEAD)
        assert result.action is InputGeometryParseAction.extracted
        assert result.origin == "orca_log"
        assert [a[0] for a in result.atoms] == ["O", "H", "H"]
        # Cycle 1's coordinates (the starting point), not cycle 2's.
        assert result.atoms[0][1:] == pytest.approx((0.0, 0.0, 0.118351))

    def test_first_block_of_real_run_extracts_eighteen_atoms(self):
        # Real ARC-produced ORCA log whose input used '* xyzfile 1 1
        # reactant.xyz' (an external file this test never sees) -- same
        # point as above, on a real fixture rather than a hand-authored one.
        result = parse_orca_log_input_geometry(ORCA_OPT_LOG)
        assert result.action is InputGeometryParseAction.extracted
        assert result.origin == "orca_log"
        assert len(result.atoms) == 18
        first_element, x, y, z = result.atoms[0]
        assert first_element == "C"
        assert (x, y, z) == pytest.approx((-2.781600, 1.622250, 1.415630))

    def test_no_block_is_not_determinable(self):
        result = parse_orca_log_input_geometry("* O R C A *\nnothing here\n")
        assert result.action is InputGeometryParseAction.not_determinable


class TestDispatch:
    def test_gaussian_input(self):
        result = extract_input_geometry(
            software="gaussian", artifact_kind=ArtifactKind.input, text=GAUSSIAN_GJF
        )
        assert result.action is InputGeometryParseAction.extracted
        assert result.origin == "gaussian_input"

    def test_gaussian_output_log(self):
        result = extract_input_geometry(
            software="gaussian",
            artifact_kind=ArtifactKind.output_log,
            text=GAUSSIAN_OPT_LOG,
        )
        assert result.action is InputGeometryParseAction.extracted
        assert result.origin == "gaussian_log"

    def test_orca_input(self):
        result = extract_input_geometry(
            software="orca", artifact_kind=ArtifactKind.input, text=ORCA_INP
        )
        assert result.action is InputGeometryParseAction.extracted
        assert result.origin == "orca_input"

    def test_orca_output_log(self):
        result = extract_input_geometry(
            software="orca",
            artifact_kind=ArtifactKind.output_log,
            text=ORCA_LOG_HEAD,
        )
        assert result.action is InputGeometryParseAction.extracted
        assert result.origin == "orca_log"

    @pytest.mark.parametrize("software", ["molpro", "psi4"])
    @pytest.mark.parametrize(
        "kind", [ArtifactKind.input, ArtifactKind.output_log]
    )
    def test_unwired_programs_are_not_determinable(self, software, kind):
        result = extract_input_geometry(software=software, artifact_kind=kind, text="anything")
        assert result.action is InputGeometryParseAction.not_determinable
        assert software in (result.reason or "")

    def test_unsupported_artifact_kind_is_not_determinable(self):
        result = extract_input_geometry(
            software="gaussian", artifact_kind=ArtifactKind.hessian, text="anything"
        )
        assert result.action is InputGeometryParseAction.not_determinable


# ---------------------------------------------------------------------------
# Bohr-unit rejection. A deck whose coordinates are in Bohr must never be
# stored as Angstrom -- that silently inflates every distance by
# 1/0.529177 = 1.8897x. Reject, don't convert (per the module's own
# "reject, don't guess" discipline).
# ---------------------------------------------------------------------------


def _gaussian_gjf(route: str) -> str:
    return (
        f"%chk=water.chk\n"
        f"{route}\n"
        "\n"
        "water\n"
        "\n"
        "0 1\n"
        "O 0.000000 0.000000 0.118351\n"
        "H 0.000000 0.761187 -0.469725\n"
        "H 0.000000 -0.761187 -0.469725\n"
        "\n"
    )


class TestGaussianBohrRejection:
    @pytest.mark.parametrize(
        "route",
        [
            "# opt units=bohr b3lyp/6-31g(d)",
            "# opt units(bohr) b3lyp/6-31g(d)",
            "# opt units=(bohr,nosymm) b3lyp/6-31g(d)",
        ],
        ids=["units=bohr", "units(bohr)", "units=(bohr,...)"],
    )
    def test_bohr_route_is_rejected(self, route):
        result = parse_gaussian_input_geometry(_gaussian_gjf(route))
        assert result.action is InputGeometryParseAction.not_determinable
        assert result.atoms is None
        assert "bohr" in (result.reason or "").lower()

    def test_angstrom_deck_is_unaffected(self):
        # Sanity check: an ordinary deck (no Units keyword) still extracts.
        result = parse_gaussian_input_geometry(_gaussian_gjf("# opt b3lyp/6-31g(d)"))
        assert result.action is InputGeometryParseAction.extracted


def _orca_xyz_deck(bang_line: str, *, coords_units_line: str = "") -> str:
    coords_block = ""
    if coords_units_line:
        coords_block = (
            "%coords\n"
            "  CTyp xyz\n"
            "  Charge 0\n"
            "  Mult 1\n"
            f"  {coords_units_line}\n"
            "  coords\n"
            "    O 0.000000 0.000000 0.118351\n"
            "    H 0.000000 0.761187 -0.469725\n"
            "    H 0.000000 -0.761187 -0.469725\n"
            "  end\n"
            "end\n"
        )
    xyz_block = ""
    if not coords_units_line:
        xyz_block = (
            "* xyz 0 1\n"
            "O 0.000000 0.000000 0.118351\n"
            "H 0.000000 0.761187 -0.469725\n"
            "H 0.000000 -0.761187 -0.469725\n"
            "*\n"
        )
    return f"{bang_line}\n\n{coords_block}{xyz_block}"


class TestOrcaBohrRejection:
    def test_bang_line_bohrs_is_rejected(self):
        result = parse_orca_input_geometry(
            _orca_xyz_deck("! B3LYP def2-TZVP Opt Bohrs")
        )
        assert result.action is InputGeometryParseAction.not_determinable
        assert "bohr" in (result.reason or "").lower()

    def test_coords_block_units_bohrs_is_rejected(self):
        result = parse_orca_input_geometry(
            _orca_xyz_deck(
                "! B3LYP def2-TZVP Opt", coords_units_line="Units Bohrs"
            )
        )
        assert result.action is InputGeometryParseAction.not_determinable
        assert "bohr" in (result.reason or "").lower()

    def test_angstrom_deck_is_unaffected(self):
        result = parse_orca_input_geometry(_orca_xyz_deck("! B3LYP def2-TZVP Opt"))
        assert result.action is InputGeometryParseAction.extracted


# ---------------------------------------------------------------------------
# Element-token normalisation: atomic numbers and Gaussian-style trailing
# label suffixes resolve; isotope/fragment syntax this module cannot
# resolve is rejected -- never handed to resolve_geometry_payload, which
# would otherwise mint a Geometry row that then fails the DB's element
# canonicality CHECK.
# ---------------------------------------------------------------------------


class TestGaussianElementTokenNormalization:
    def test_atomic_number_column_resolves(self):
        text = _gaussian_gjf("# opt b3lyp/6-31g(d)").replace("O 0.000000", "8 0.000000")
        result = parse_gaussian_input_geometry(text)
        assert result.action is InputGeometryParseAction.extracted
        assert result.atoms[0][0] == "O"

    def test_labelled_element_suffix_resolves(self):
        text = _gaussian_gjf("# opt b3lyp/6-31g(d)").replace("O 0.000000", "O1 0.000000")
        result = parse_gaussian_input_geometry(text)
        assert result.action is InputGeometryParseAction.extracted
        assert result.atoms[0][0] == "O"

    def test_deuterium_symbol_is_not_rejected(self):
        # D/T are legitimate deposited symbols (ADR 0008), not something
        # this module's element-token check may treat as unresolvable.
        text = _gaussian_gjf("# opt b3lyp/6-31g(d)").replace(
            "H 0.000000 0.761187", "D 0.000000 0.761187"
        )
        result = parse_gaussian_input_geometry(text)
        assert result.action is InputGeometryParseAction.extracted
        assert result.atoms[1][0] == "D"

    @pytest.mark.parametrize("token", ["C-0.5", "C-13", "1C"])
    def test_unresolvable_token_is_rejected(self, token):
        text = _gaussian_gjf("# opt b3lyp/6-31g(d)").replace("O 0.000000", f"{token} 0.000000")
        result = parse_gaussian_input_geometry(text)
        assert result.action is InputGeometryParseAction.not_determinable
        assert token in (result.reason or "")


class TestOrcaElementTokenNormalization:
    def test_atomic_number_column_resolves(self):
        text = _orca_xyz_deck("! B3LYP def2-TZVP Opt").replace("O 0.000000", "8 0.000000")
        result = parse_orca_input_geometry(text)
        assert result.action is InputGeometryParseAction.extracted
        assert result.atoms[0][0] == "O"

    def test_labelled_element_suffix_resolves(self):
        text = _orca_xyz_deck("! B3LYP def2-TZVP Opt").replace("O 0.000000", "O1 0.000000")
        result = parse_orca_input_geometry(text)
        assert result.action is InputGeometryParseAction.extracted
        assert result.atoms[0][0] == "O"

    @pytest.mark.parametrize("token", ["C-0.5", "C-13", "1C"])
    def test_unresolvable_token_is_rejected(self, token):
        text = _orca_xyz_deck("! B3LYP def2-TZVP Opt").replace("O 0.000000", f"{token} 0.000000")
        result = parse_orca_input_geometry(text)
        assert result.action is InputGeometryParseAction.not_determinable
        assert token in (result.reason or "")
