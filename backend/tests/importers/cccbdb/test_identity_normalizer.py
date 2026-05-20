"""Identity-normalizer tests for the CCCBDB importer."""

from __future__ import annotations

import pytest

from app.importers.cccbdb.normalizers.identity import (
    collapse_whitespace,
    infer_multiplicity_from_state,
    normalize_cas,
    normalize_formula,
    normalize_inchi,
    normalize_inchikey,
    normalize_smiles,
    parse_int_or_none,
    parse_state_label,
)


class TestCollapseWhitespace:
    def test_strips_and_collapses(self):
        assert collapse_whitespace("  Water   vapor  ") == "Water vapor"

    def test_none_returns_none(self):
        assert collapse_whitespace(None) is None

    def test_empty_returns_none(self):
        assert collapse_whitespace("   ") is None


class TestNormalizeFormula:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (" H2O ", "H2O"),
            ("C 6 H 6", "C6H6"),
            ("  CH4  ", "CH4"),
            (None, None),
        ],
    )
    def test_formula(self, raw, expected):
        assert normalize_formula(raw) == expected


class TestNormalizeInChIKey:
    def test_uppercases(self):
        assert (
            normalize_inchikey("xlyofnoqvpjjnp-uhfffaoysa-n")
            == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        )

    def test_strips(self):
        assert (
            normalize_inchikey(" XLYOFNOQVPJJNP-UHFFFAOYSA-N\n")
            == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        )

    def test_none(self):
        assert normalize_inchikey(None) is None


class TestNormalizeInChI:
    def test_strips_only(self):
        assert (
            normalize_inchi(" InChI=1S/H2O/h1H2 ")
            == "InChI=1S/H2O/h1H2"
        )

    def test_does_not_validate_prefix(self):
        # Phase 1 is permissive; a missing prefix is preserved verbatim.
        assert normalize_inchi("1S/H2O/h1H2") == "1S/H2O/h1H2"


class TestNormalizeSMILES:
    def test_strips_only(self):
        assert normalize_smiles("  O  ") == "O"

    def test_aromatic_kept(self):
        # Phase 1 does NOT canonicalize via RDKit; aromatic SMILES is
        # preserved as-is.
        assert normalize_smiles("c1ccccc1") == "c1ccccc1"


class TestNormalizeCAS:
    def test_strips(self):
        assert normalize_cas(" 7732-18-5 ") == "7732-18-5"


class TestParseIntOrNone:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("0", 0),
            ("  1 ", 1),
            ("12", 12),
            ("", None),
            (None, None),
            ("abc", None),
            ("-", None),
        ],
    )
    def test_parse(self, raw, expected):
        assert parse_int_or_none(raw) == expected


class TestParseStateLabel:
    def test_preserves_term_symbol(self):
        assert parse_state_label("X 1A1") == "X 1A1"

    def test_collapses_whitespace(self):
        assert parse_state_label("  X    2Pi  ") == "X 2Pi"

    def test_none(self):
        assert parse_state_label(None) is None


class TestInferMultiplicityFromState:
    @pytest.mark.parametrize(
        "label,expected",
        [
            ("X 1A1", 1),
            ("X 2Pi", 2),
            ("a 3B1", 3),
            ("X 1Sigmag+", 1),
            (None, None),
            ("ground state", None),
        ],
    )
    def test_inference(self, label, expected):
        assert infer_multiplicity_from_state(label) == expected
