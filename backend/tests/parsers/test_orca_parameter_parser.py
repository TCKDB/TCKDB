"""Tests for the ORCA log file parser.

Validates against the real sp_orca.log file (DLPNO-CCSD(T)/cc-pVTZ-F12 single point).
"""

from __future__ import annotations

import os

import pytest

from app.services.orca_parameter_parser import (
    _extract_input_block,
    _parse_block_sections,
    _parse_keyword_lines,
    parse_charge_multiplicity,
    parse_method_basis,
    parse_orca_log,
    parse_software_version,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
ORCA_DIR = os.path.join(FIXTURES_DIR, "orca")
SP_LOG = os.path.join(ORCA_DIR, "sp_orca.log")


@pytest.fixture
def orca_text() -> str:
    with open(SP_LOG) as f:
        return f.read()


@pytest.fixture
def input_lines(orca_text) -> list[str]:
    return _extract_input_block(orca_text)


def _find_param(params: list[dict], raw_key: str, section: str | None = None) -> dict | None:
    for p in params:
        if p["raw_key"].lower() == raw_key.lower():
            if section is None or p.get("section") == section:
                return p
    return None


# ---------------------------------------------------------------------------
# Input block extraction
# ---------------------------------------------------------------------------


class TestInputBlockExtraction:
    def test_extracts_input_lines(self, input_lines):
        assert len(input_lines) > 0

    def test_keyword_lines_present(self, input_lines):
        keyword_lines = [line for line in input_lines if line.strip().startswith("!")]
        assert len(keyword_lines) >= 1

    def test_block_sections_present(self, input_lines):
        block_lines = [line for line in input_lines if line.strip().startswith("%")]
        assert len(block_lines) >= 1

    def test_coordinate_header_present(self, input_lines):
        coord_lines = [line for line in input_lines if line.strip().startswith("*")]
        assert len(coord_lines) >= 1


# ---------------------------------------------------------------------------
# Software version
# ---------------------------------------------------------------------------


class TestSoftwareVersion:
    def test_extracts_version(self, orca_text):
        sv = parse_software_version(orca_text)
        assert sv is not None
        assert sv["name"] == "orca"
        assert sv["version"] == "5.0.4"

    def test_no_build_for_orca(self, orca_text):
        sv = parse_software_version(orca_text)
        assert sv["build"] is None


# ---------------------------------------------------------------------------
# Charge / multiplicity
# ---------------------------------------------------------------------------


class TestChargeMult:
    def test_charge_zero(self, orca_text):
        cm = parse_charge_multiplicity(orca_text)
        assert cm is not None
        assert cm["charge"] == 0

    def test_multiplicity_two(self, orca_text):
        cm = parse_charge_multiplicity(orca_text)
        assert cm["multiplicity"] == 2


# ---------------------------------------------------------------------------
# Method / basis
# ---------------------------------------------------------------------------


class TestMethodBasis:
    def test_method_extracted(self, orca_text):
        mb = parse_method_basis(orca_text)
        assert mb is not None
        assert mb["method"].lower() == "dlpno-ccsd(t)"

    def test_basis_extracted(self, orca_text):
        mb = parse_method_basis(orca_text)
        assert mb["basis"].lower() == "cc-pvtz-f12"

    def test_aux_basis_extracted(self, orca_text):
        mb = parse_method_basis(orca_text)
        assert mb["aux_basis"].lower() == "aug-cc-pvtz/c"

    def test_cabs_basis_extracted(self, orca_text):
        mb = parse_method_basis(orca_text)
        assert mb["cabs_basis"].lower() == "cc-pvtz-f12-cabs"


# ---------------------------------------------------------------------------
# Keyword line parsing
# ---------------------------------------------------------------------------


class TestKeywordLineParsing:
    def test_tightscf_parsed(self, input_lines):
        params = _parse_keyword_lines(input_lines)
        p = _find_param(params, "tightscf")
        assert p is not None
        assert p["canonical_key"] == "scf.convergence"
        assert p["canonical_value"] == "tight"
        assert p["section"] == "scf"

    def test_normalpno_parsed(self, input_lines):
        params = _parse_keyword_lines(input_lines)
        p = _find_param(params, "normalpno")
        assert p is not None
        assert p["canonical_key"] == "pno.truncation"
        assert p["canonical_value"] == "normal"

    def test_method_not_stored_as_parameter(self, input_lines):
        params = _parse_keyword_lines(input_lines)
        assert _find_param(params, "dlpno-ccsd(t)") is None

    def test_basis_not_stored_as_parameter(self, input_lines):
        params = _parse_keyword_lines(input_lines)
        assert _find_param(params, "cc-pvtz-f12") is None

    def test_aux_basis_not_stored_as_parameter(self, input_lines):
        params = _parse_keyword_lines(input_lines)
        assert _find_param(params, "aug-cc-pvtz/c") is None

    def test_sp_not_stored_as_parameter(self, input_lines):
        """Job type 'sp' is not a parameter."""
        params = _parse_keyword_lines(input_lines)
        assert _find_param(params, "sp") is None

    def test_uhf_not_stored_as_parameter(self, input_lines):
        """Method keyword 'uHF' is not a parameter."""
        params = _parse_keyword_lines(input_lines)
        assert _find_param(params, "uhf") is None


# ---------------------------------------------------------------------------
# Block section parsing
# ---------------------------------------------------------------------------


class TestBlockSectionParsing:
    def test_maxcore_parsed(self, input_lines):
        params = _parse_block_sections(input_lines)
        p = _find_param(params, "maxcore")
        assert p is not None
        assert p["raw_value"] == "4096"
        assert p["canonical_key"] == "memory.maxcore_mb"
        assert p["section"] == "memory"
        assert p["unit"] == "MB"

    def test_nprocs_parsed(self, input_lines):
        params = _parse_block_sections(input_lines)
        p = _find_param(params, "nprocs")
        assert p is not None
        assert p["raw_value"] == "8"
        assert p["canonical_key"] == "parallel.nproc"
        assert p["section"] == "parallel"

    def test_scf_maxiter_parsed(self, input_lines):
        params = _parse_block_sections(input_lines)
        p = _find_param(params, "MaxIter", section="scf")
        assert p is not None
        assert p["raw_value"] == "500"
        assert p["canonical_key"] == "scf.max_cycles"

    def test_comments_stripped(self, input_lines):
        """Inline comments (# ...) should not appear in parameter values."""
        params = _parse_block_sections(input_lines)
        for p in params:
            assert "#" not in p["raw_value"]


# ---------------------------------------------------------------------------
# Full parse_orca_log
# ---------------------------------------------------------------------------


class TestFullParse:
    def test_parser_version(self):
        result = parse_orca_log(path=SP_LOG)
        assert result["parser_version"] == "orca_v2"

    def test_total_parameter_count(self):
        result = parse_orca_log(path=SP_LOG)
        # tightscf, normalpno, maxcore, nprocs, MaxIter = 5
        assert len(result["parameters"]) == 5

    def test_parameters_json_has_input_lines(self):
        result = parse_orca_log(path=SP_LOG)
        assert "input_lines" in result["parameters_json"]
        assert len(result["parameters_json"]["input_lines"]) > 0


# ---------------------------------------------------------------------------
# Cross-software canonical key consistency
# ---------------------------------------------------------------------------


class TestCrossSoftwareConsistency:
    """Verify that ORCA and Gaussian share canonical keys for equivalent concepts."""

    def test_scf_convergence_shared(self):
        """Both parsers map tight SCF convergence to the same canonical key."""
        from app.services.gaussian_parameter_parser import (
            _parse_route_tokens as gaussian_parse,
        )

        g_params = gaussian_parse("#P scf=(tight) uwb97xd/def2tzvp")
        o_result = parse_orca_log(path=SP_LOG)

        g_scf = next(
            (p for p in g_params if p.get("canonical_key") == "scf.convergence"),
            None,
        )
        o_scf = next(
            (p for p in o_result["parameters"] if p.get("canonical_key") == "scf.convergence"),
            None,
        )

        assert g_scf is not None
        assert o_scf is not None
        # Same canonical value despite different raw syntax
        assert g_scf["canonical_value"] == o_scf["canonical_value"] == "tight"
        # Different raw keys (software-specific)
        assert g_scf["raw_key"] == "tight"
        assert o_scf["raw_key"] == "tightscf"

    def test_scf_max_cycles_shared(self):
        """Both parsers map SCF max iterations to scf.max_cycles."""
        from app.services.gaussian_parameter_parser import (
            _parse_route_tokens as gaussian_parse,
        )

        g_params = gaussian_parse("#P scf=(maxcycle=200) uwb97xd/def2tzvp")
        o_result = parse_orca_log(path=SP_LOG)

        g_mc = next(
            (p for p in g_params if p.get("canonical_key") == "scf.max_cycles"),
            None,
        )
        o_mc = next(
            (p for p in o_result["parameters"] if p.get("canonical_key") == "scf.max_cycles"),
            None,
        )

        assert g_mc is not None
        assert o_mc is not None
        assert g_mc["canonical_key"] == o_mc["canonical_key"] == "scf.max_cycles"


# ---------------------------------------------------------------------------
# Raw .in input artifact parsing (no log echo markers, no `|N>` prefix)
# ---------------------------------------------------------------------------


RAW_INPUT_EXAMPLE = """\
!uHF dlpno-ccsd(t)-f12 cc-pvtz-f12 aug-cc-pvtz/c cc-pvtz-f12-cabs tightscf normalpno
!sp

%maxcore 3158
%pal nprocs 12 end

* xyz 0 3
C       1.07668300   -1.68376800    1.29309400
C       1.00200500   -0.36648300    0.91631200
H       0.74480000   -2.47051200    0.62998700
*

%scf
MaxIter 999
end
"""


def _by_canonical(params: list[dict], canonical_key: str) -> dict | None:
    for p in params:
        if p.get("canonical_key") == canonical_key:
            return p
    return None


class TestRawInputArtifact:
    """Parse a raw ORCA ``.in`` artifact (no INPUT FILE echo markers)."""

    def test_input_lines_extracted(self):
        # _extract_input_block must fall back to raw layout when the
        # log echo markers are absent.
        lines = _extract_input_block(RAW_INPUT_EXAMPLE)
        assert any(line.lstrip().startswith("!") for line in lines)
        assert any(line.lstrip().startswith("%maxcore") for line in lines)

    def test_tightscf_parsed(self):
        result = parse_orca_log(text=RAW_INPUT_EXAMPLE)
        p = _by_canonical(result["parameters"], "scf.convergence")
        assert p is not None
        assert p["raw_key"].lower() == "tightscf"
        assert p["canonical_value"] == "tight"
        assert p["section"] == "scf"
        assert p["value_type"] == "enum"

    def test_normalpno_parsed(self):
        result = parse_orca_log(text=RAW_INPUT_EXAMPLE)
        p = _by_canonical(result["parameters"], "pno.truncation")
        assert p is not None
        assert p["raw_key"].lower() == "normalpno"
        assert p["canonical_value"] == "normal"
        assert p["section"] == "pno"
        assert p["value_type"] == "enum"

    def test_sp_jobtype_not_a_parameter(self):
        # `!sp` is a job type, not a parameter row.
        result = parse_orca_log(text=RAW_INPUT_EXAMPLE)
        for p in result["parameters"]:
            assert p["raw_key"].lower() != "sp"

    def test_method_basis_tokens_not_parameters(self):
        # Method/basis tokens belong in level_of_theory, not parameters.
        result = parse_orca_log(text=RAW_INPUT_EXAMPLE)
        forbidden = {
            "dlpno-ccsd(t)-f12",
            "cc-pvtz-f12",
            "aug-cc-pvtz/c",
            "cc-pvtz-f12-cabs",
            "uhf",
        }
        present = {p["raw_key"].lower() for p in result["parameters"]}
        assert forbidden.isdisjoint(present)

    def test_maxcore_block(self):
        result = parse_orca_log(text=RAW_INPUT_EXAMPLE)
        p = _by_canonical(result["parameters"], "memory.maxcore_mb")
        assert p is not None
        assert p["raw_value"] == "3158"
        assert p["canonical_value"] == "3158"
        assert p["section"] == "memory"
        assert p["value_type"] == "int"
        assert p["unit"] == "MB"

    def test_pal_single_line(self):
        result = parse_orca_log(text=RAW_INPUT_EXAMPLE)
        p = _by_canonical(result["parameters"], "parallel.nproc")
        assert p is not None
        assert p["raw_key"].lower() == "nprocs"
        assert p["raw_value"] == "12"
        assert p["canonical_value"] == "12"
        assert p["section"] == "parallel"
        assert p["value_type"] == "int"

    def test_scf_maxiter_multiline(self):
        result = parse_orca_log(text=RAW_INPUT_EXAMPLE)
        p = _by_canonical(result["parameters"], "scf.max_cycles")
        assert p is not None
        assert p["raw_key"] == "MaxIter"
        assert p["raw_value"] == "999"
        assert p["canonical_value"] == "999"
        assert p["section"] == "scf"
        assert p["value_type"] == "int"

    def test_coordinate_block_ignored(self):
        # No carbon/hydrogen atom rows from the `* xyz` block should
        # surface as parameter raw_keys.
        result = parse_orca_log(text=RAW_INPUT_EXAMPLE)
        raw_keys = {p["raw_key"] for p in result["parameters"]}
        # Atom symbols would only show up if the coord block leaked.
        assert "C" not in raw_keys
        assert "H" not in raw_keys

    def test_charge_multiplicity_from_raw_input(self):
        # Charge/mult still readable from the raw `* xyz 0 3` header.
        cm = parse_charge_multiplicity(RAW_INPUT_EXAMPLE)
        assert cm == {"charge": 0, "multiplicity": 3}

    def test_method_basis_from_raw_input(self):
        # LoT extraction uses the same input-block scan.
        mb = parse_method_basis(RAW_INPUT_EXAMPLE)
        assert mb is not None
        assert mb["method"].lower() == "dlpno-ccsd(t)-f12"
        assert mb["basis"].lower() == "cc-pvtz-f12"
        assert mb["aux_basis"].lower() == "aug-cc-pvtz/c"
        assert mb["cabs_basis"].lower() == "cc-pvtz-f12-cabs"

    def test_full_expected_parameter_set(self):
        # Exactly the five parameter rows the task expects.
        result = parse_orca_log(text=RAW_INPUT_EXAMPLE)
        canonical_keys = sorted(
            p["canonical_key"]
            for p in result["parameters"]
            if p["canonical_key"] is not None
        )
        assert canonical_keys == sorted([
            "scf.convergence",
            "pno.truncation",
            "memory.maxcore_mb",
            "parallel.nproc",
            "scf.max_cycles",
        ])


class TestPalSingleLineSection:
    """Single-line ``%pal nprocs N end`` syntax — separate fixture."""

    def test_pal_single_line_no_other_content(self):
        text = (
            "!sp hf/sto-3g\n"
            "%pal nprocs 16 end\n"
            "* xyz 0 1\n"
            "H 0 0 0\n"
            "H 0 0 0.74\n"
            "*\n"
        )
        result = parse_orca_log(text=text)
        p = _by_canonical(result["parameters"], "parallel.nproc")
        assert p is not None
        assert p["raw_value"] == "16"
        assert p["section"] == "parallel"


class TestScfBlockSingleLine:
    """Single-line ``%scf MaxIter N end`` syntax."""

    def test_scf_single_line(self):
        text = (
            "!sp hf/sto-3g\n"
            "%scf MaxIter 777 end\n"
            "* xyz 0 1\n"
            "H 0 0 0\n"
            "H 0 0 0.74\n"
            "*\n"
        )
        result = parse_orca_log(text=text)
        p = _by_canonical(result["parameters"], "scf.max_cycles")
        assert p is not None
        assert p["raw_key"] == "MaxIter"
        assert p["raw_value"] == "777"
        assert p["section"] == "scf"


class TestPalMultiLine:
    """Multi-line ``%pal\\n  nprocs N\\nend`` syntax."""

    def test_pal_multi_line(self):
        text = (
            "!sp hf/sto-3g\n"
            "%pal\n"
            "  nprocs 24\n"
            "end\n"
            "* xyz 0 1\n"
            "H 0 0 0\n"
            "*\n"
        )
        result = parse_orca_log(text=text)
        p = _by_canonical(result["parameters"], "parallel.nproc")
        assert p is not None
        assert p["raw_value"] == "24"
        assert p["section"] == "parallel"
