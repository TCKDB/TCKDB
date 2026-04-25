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
        keyword_lines = [l for l in input_lines if l.strip().startswith("!")]
        assert len(keyword_lines) >= 1

    def test_block_sections_present(self, input_lines):
        block_lines = [l for l in input_lines if l.strip().startswith("%")]
        assert len(block_lines) >= 1

    def test_coordinate_header_present(self, input_lines):
        coord_lines = [l for l in input_lines if l.strip().startswith("*")]
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
        assert p["canonical_key"] == "scf_convergence"
        assert p["canonical_value"] == "tight"
        assert p["section"] == "scf"

    def test_normalpno_parsed(self, input_lines):
        params = _parse_keyword_lines(input_lines)
        p = _find_param(params, "normalpno")
        assert p is not None
        assert p["canonical_key"] == "pno_truncation"
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
        assert p["canonical_key"] == "maxcore_mb"
        assert p["section"] == "resource"

    def test_nprocs_parsed(self, input_lines):
        params = _parse_block_sections(input_lines)
        p = _find_param(params, "nprocs")
        assert p is not None
        assert p["raw_value"] == "8"
        assert p["canonical_key"] == "nproc"
        assert p["section"] == "resource"

    def test_scf_maxiter_parsed(self, input_lines):
        params = _parse_block_sections(input_lines)
        p = _find_param(params, "MaxIter", section="scf")
        assert p is not None
        assert p["raw_value"] == "500"
        assert p["canonical_key"] == "scf_max_cycles"

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
        assert result["parser_version"] == "orca_v1"

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
            (p for p in g_params if p.get("canonical_key") == "scf_convergence"),
            None,
        )
        o_scf = next(
            (p for p in o_result["parameters"] if p.get("canonical_key") == "scf_convergence"),
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
        """Both parsers map SCF max iterations to scf_max_cycles."""
        from app.services.gaussian_parameter_parser import (
            _parse_route_tokens as gaussian_parse,
        )

        g_params = gaussian_parse("#P scf=(maxcycle=200) uwb97xd/def2tzvp")
        o_result = parse_orca_log(path=SP_LOG)

        g_mc = next(
            (p for p in g_params if p.get("canonical_key") == "scf_max_cycles"),
            None,
        )
        o_mc = next(
            (p for p in o_result["parameters"] if p.get("canonical_key") == "scf_max_cycles"),
            None,
        )

        assert g_mc is not None
        assert o_mc is not None
        assert g_mc["canonical_key"] == o_mc["canonical_key"] == "scf_max_cycles"
