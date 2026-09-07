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
