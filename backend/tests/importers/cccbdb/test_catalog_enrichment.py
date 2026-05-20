"""Tests for ``propose_catalog_matches``: scored, ambiguity-honest
identity-enrichment candidates from the CCCBDB molecule catalog.

These tests pin the contract:

* Multiple isomers per formula are NEVER silently collapsed.
* Formula-only matches with multiple catalog candidates are always
  ambiguous, regardless of confidence score.
* The original property row is never mutated by the helper.
* Ambiguous candidates are returned with a warning, not dropped.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.importers.cccbdb.enrichment import propose_catalog_matches
from app.importers.cccbdb.models import (
    CCCBDBCatalogMatchConfidence,
    CCCBDBExperimentalPropertyRow,
)
from app.importers.cccbdb.parsers import parse_molecule_catalog_page

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)


@pytest.fixture(scope="module")
def catalog():
    html = (FIXTURES_DIR / "catalog_inchix.html").read_text(encoding="utf-8")
    return parse_molecule_catalog_page(
        html, source_url="https://cccbdb.nist.gov/inchix.asp"
    )


def _row(**kwargs):
    base = {"row_index": 0}
    base.update(kwargs)
    return CCCBDBExperimentalPropertyRow(**base)


class TestUnambiguousMatches:
    def test_formula_plus_exact_name_is_high_unambiguous(self, catalog):
        matches = propose_catalog_matches(
            _row(formula="H2O", name="Water"), catalog
        )
        assert len(matches) == 1
        assert matches[0].score == CCCBDBCatalogMatchConfidence.high
        assert matches[0].is_unambiguous is True
        assert matches[0].catalog_entry.inchikey == (
            "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        )

    def test_unique_formula_only_is_low_but_unambiguous(self, catalog):
        # H2 has only one catalog entry.
        matches = propose_catalog_matches(_row(formula="H2"), catalog)
        assert len(matches) == 1
        assert matches[0].score == CCCBDBCatalogMatchConfidence.low
        assert matches[0].is_unambiguous is True
        # The match is low-confidence so callers can still gate on it.
        assert "formula match only" in matches[0].match_reasons

    def test_name_only_unique_is_medium_unambiguous(self, catalog):
        # The fixture has exactly one "Cyclopropane" entry.
        matches = propose_catalog_matches(_row(name="Cyclopropane"), catalog)
        assert len(matches) == 1
        assert matches[0].score == CCCBDBCatalogMatchConfidence.medium
        assert matches[0].is_unambiguous is True


class TestAmbiguousMatches:
    def test_formula_only_with_two_isomers_returns_both_as_ambiguous(
        self, catalog
    ):
        matches = propose_catalog_matches(_row(formula="C2H6O"), catalog)
        names = {m.catalog_entry.name for m in matches}
        assert names == {"Ethanol", "Dimethyl ether"}
        for m in matches:
            assert m.score == CCCBDBCatalogMatchConfidence.low
            assert m.is_unambiguous is False
            assert any("isomer ambiguity" in w for w in m.warnings)

    def test_formula_plus_name_picks_one_high_isomer_keeps_other_ambiguous(
        self, catalog
    ):
        """C2H6O + name='Ethanol' should produce ethanol at high
        confidence (unambiguous) and dimethyl ether at low confidence
        (ambiguous), both returned for transparency."""

        matches = propose_catalog_matches(
            _row(formula="C2H6O", name="Ethanol"), catalog
        )
        assert len(matches) == 2
        by_name = {m.catalog_entry.name: m for m in matches}
        assert by_name["Ethanol"].score == CCCBDBCatalogMatchConfidence.high
        assert by_name["Ethanol"].is_unambiguous is True
        assert by_name["Dimethyl ether"].score == (
            CCCBDBCatalogMatchConfidence.low
        )
        assert by_name["Dimethyl ether"].is_unambiguous is False

    def test_c3h6_isomers_both_returned(self, catalog):
        # Propene vs cyclopropane: same formula, different names.
        matches = propose_catalog_matches(_row(formula="C3H6"), catalog)
        assert {m.catalog_entry.name for m in matches} == {
            "Propene",
            "Cyclopropane",
        }
        for m in matches:
            assert m.is_unambiguous is False


class TestNonMatches:
    def test_conflicting_formula_returns_no_match(self, catalog):
        # Property row says "C99H99" — no catalog entry has that
        # formula. Even if the name happens to overlap, we must NOT
        # match across a conflicting formula.
        matches = propose_catalog_matches(
            _row(formula="C99H99", name="Water"), catalog
        )
        assert matches == []

    def test_unknown_formula_unknown_name_returns_empty(self, catalog):
        matches = propose_catalog_matches(
            _row(formula="XYZ", name="Mystery"), catalog
        )
        assert matches == []

    def test_empty_row_returns_empty(self, catalog):
        matches = propose_catalog_matches(_row(), catalog)
        assert matches == []


class TestImmutability:
    def test_row_is_not_mutated(self, catalog):
        row = _row(formula="C2H6O", name="Ethanol")
        before = copy.deepcopy(row)
        propose_catalog_matches(row, catalog)
        assert row == before

    def test_catalog_entry_objects_returned_match_catalog_objects(self, catalog):
        matches = propose_catalog_matches(_row(formula="H2O", name="Water"), catalog)
        assert matches[0].catalog_entry is next(
            e for e in catalog.entries if e.name == "Water"
        )


class TestAmbiguousMatchNeverPromotesIdentity:
    """The helper must not silently expose an enriched InChI/InChIKey
    as a *resolved* identity when the match is ambiguous. Callers see
    the candidate plus its warnings; they decide whether to trust it."""

    def test_low_confidence_ambiguous_matches_keep_is_unambiguous_false(
        self, catalog
    ):
        matches = propose_catalog_matches(_row(formula="C2H6O"), catalog)
        for m in matches:
            assert m.is_unambiguous is False
            assert any("ambiguity" in w for w in m.warnings)

    def test_ambiguous_candidates_carry_full_catalog_entry(self, catalog):
        """Even an ambiguous match returns the full ``catalog_entry``
        — the caller is responsible for deciding whether to extract
        InChI/InChIKey/SMILES from it. The helper just doesn't
        promote it to ``is_unambiguous``."""

        matches = propose_catalog_matches(_row(formula="C2H6O"), catalog)
        for m in matches:
            assert m.catalog_entry.inchi is not None
            assert m.catalog_entry.inchikey is not None
