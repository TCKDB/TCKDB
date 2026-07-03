"""Tests for the CCCBDB choosex.asp parser
(``parsers/species_selection.py``).

All tests are offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers.cccbdb.parsers.species_selection import (
    canonicalize_cas,
    parse_species_selection_page,
    structural_to_hill_formula,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def page():
    return parse_species_selection_page(
        _load("form_result_choose_c2h6o_live.html"),
        base_url="https://cccbdb.nist.gov/choosex.asp",
    )


def test_form_metadata_extracted(page):
    assert page.title == "CCCBDB choose from several molecules"
    assert page.heading == "Choose which species"
    assert page.form_action_url == "https://cccbdb.nist.gov/fixchoicex.asp"
    assert page.form_method == "POST"


def test_three_candidates_extracted(page):
    # Live shape: DME + 2 ethanol conformers, all checked by default.
    assert len(page.candidates) == 3


def test_first_candidate_is_dme(page):
    c = page.candidates[0]
    assert c.name == "Dimethyl ether"
    # <sub>...</sub> collapsed to inline digits.
    assert c.formula == "CH3OCH3"
    assert c.cas_number == "115106"
    assert c.form_field_name == "choice"
    assert c.form_field_value == "115106"


def test_ethanol_rows_share_choice_value(page):
    ethanols = [c for c in page.candidates if c.name == "Ethanol"]
    assert len(ethanols) == 2
    # Same CAS → same POST payload, even though they're separate
    # conformer rows on the live page.
    assert {c.form_field_value for c in ethanols} == {"64175"}
    # Configurations differ (1 vs 2).
    assert {c.config for c in ethanols} == {"1", "2"}


def test_candidate_form_fields_are_post_ready(page):
    ethanol = next(c for c in page.candidates if c.name == "Ethanol")
    fields = ethanol.form_fields()
    assert fields["choice"] == "64175"
    # The CCCBDB selection form requires this submit-button name.
    assert fields["submitselect"] == "Select"


def test_raw_row_preserves_column_text(page):
    dme = page.candidates[0]
    assert dme.raw_row  # at least populated
    # The row number ("1"), the structural formula text, and the
    # name should all be retrievable as raw text.
    assert any(v == "1" for v in dme.raw_row.values())
    assert any(v == "Dimethyl ether" for v in dme.raw_row.values())


# ---------------------------------------------------------------------------
# Degenerate inputs
# ---------------------------------------------------------------------------


def test_no_selection_form_emits_warning():
    page = parse_species_selection_page(
        "<html><body><p>no form</p></body></html>",
        base_url="https://cccbdb.nist.gov/choosex.asp",
    )
    assert page.candidates == []
    assert any(
        "no form posting to fixchoicex" in w for w in page.warnings
    )


def test_empty_candidate_table_emits_warning():
    page = parse_species_selection_page(
        '<html><body>'
        '<FORM ACTION="fixchoicex.asp" METHOD="post" id="form1">'
        '<table></table></FORM></body></html>',
        base_url="https://cccbdb.nist.gov/choosex.asp",
    )
    assert page.candidates == []
    assert any("no candidates extracted" in w for w in page.warnings)


# ---------------------------------------------------------------------------
# Helpers: CAS + Hill-system derivation
# ---------------------------------------------------------------------------


class TestCanonicalizeCas:
    def test_hyphens_stripped(self):
        assert canonicalize_cas("64-17-5") == "64175"

    def test_compact_form_preserved(self):
        assert canonicalize_cas("64175") == "64175"

    def test_whitespace_stripped(self):
        assert canonicalize_cas("  64-17-5 ") == "64175"

    def test_none_returns_none(self):
        assert canonicalize_cas(None) is None

    def test_blank_returns_none(self):
        assert canonicalize_cas("") is None
        assert canonicalize_cas("   ") is None


class TestStructuralToHillFormula:
    def test_ethanol_structural_to_molecular(self):
        assert structural_to_hill_formula("CH3CH2OH") == "C2H6O"

    def test_dme_structural_to_molecular(self):
        assert structural_to_hill_formula("CH3OCH3") == "C2H6O"

    def test_molecular_input_passes_through(self):
        # ``C2H6O`` is already in Hill order.
        assert structural_to_hill_formula("C2H6O") == "C2H6O"

    def test_water(self):
        assert structural_to_hill_formula("H2O") == "H2O"

    def test_methanol(self):
        assert structural_to_hill_formula("CH3OH") == "CH4O"

    def test_diatomic_count_one_omitted(self):
        assert structural_to_hill_formula("CO") == "CO"

    def test_multichar_atom(self):
        # Chlorine is "Cl" (uppercase + lowercase); ``CHCl3`` →
        # C: 1, H: 1, Cl: 3 → "CHCl3".
        assert structural_to_hill_formula("CHCl3") == "CHCl3"

    def test_parens_return_none(self):
        # We don't parse parenthesized groups; better to fall back
        # to literal comparison than to silently mis-derive.
        assert structural_to_hill_formula("(CH3)3N") is None

    def test_invalid_input_returns_none(self):
        assert structural_to_hill_formula("") is None
        assert structural_to_hill_formula(None) is None
        assert structural_to_hill_formula("3xyz") is None
