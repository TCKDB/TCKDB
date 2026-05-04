"""Tests for Gaussian ``NoSymm`` parameter extraction.

``NoSymm`` is a route-level standalone keyword that disables molecular
symmetry. It must land under ``section='symmetry'`` with canonical key
``symmetry.disabled`` regardless of source-text casing.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationParameterVocab
from app.services.gaussian_parameter_parser import _parse_route_tokens


def _find(params, raw_key: str, section: str | None = None):
    for p in params:
        if p["raw_key"] == raw_key and (section is None or p["section"] == section):
            return p
    return None


class TestNoSymmRouteParsing:
    def test_lowercase_nosymm_emits_canonical_row(self):
        params = _parse_route_tokens(
            "#P opt=(calcfc) nosymm uwb97xd/def2tzvp"
        )
        row = _find(params, "nosymm", section="symmetry")
        assert row is not None
        assert row["canonical_key"] == "symmetry.disabled"
        assert row["raw_value"] == "true"
        assert row["canonical_value"] == "true"
        assert row["value_type"] == "bool"

    def test_mixed_case_nosymm(self):
        params = _parse_route_tokens(
            "#P opt=(calcfc) NoSymm uwb97xd/def2tzvp"
        )
        # raw_key must be normalised to lowercase so downstream consumers
        # can rely on a single canonical spelling.
        row = _find(params, "nosymm", section="symmetry")
        assert row is not None
        assert row["canonical_key"] == "symmetry.disabled"
        assert row["canonical_value"] == "true"

    def test_uppercase_nosymm(self):
        params = _parse_route_tokens(
            "#P opt=(calcfc) NOSYMM uwb97xd/def2tzvp"
        )
        row = _find(params, "nosymm", section="symmetry")
        assert row is not None
        assert row["canonical_key"] == "symmetry.disabled"

    def test_nosymm_lands_in_symmetry_not_general(self):
        params = _parse_route_tokens("#P nosymm uwb97xd/def2tzvp")
        # No row should sit under section='general' for this keyword.
        general_nosymm = _find(params, "nosymm", section="general")
        assert general_nosymm is None

    def test_route_without_nosymm_emits_no_symmetry_row(self):
        params = _parse_route_tokens(
            "#P opt=(calcfc) uwb97xd/def2tzvp"
        )
        symmetry_rows = [p for p in params if p["section"] == "symmetry"]
        assert symmetry_rows == []

    def test_force_keyword_still_lands_in_general(self):
        # Regression: refactoring nosymm out of the general bucket must
        # not also pull force/test out of it.
        params = _parse_route_tokens("#P force uwb97xd/def2tzvp")
        force = _find(params, "force", section="general")
        assert force is not None
        assert force["raw_value"] == "true"


class TestNoSymmVocabSeed:
    def test_vocab_row_present(self, db_session: Session):
        row = db_session.scalar(
            select(CalculationParameterVocab).where(
                CalculationParameterVocab.canonical_key == "symmetry.disabled"
            )
        )
        assert row is not None
        assert row.expected_value_type == "bool"
        assert row.affects_scientific_result is True
        assert row.affects_numerics is True
        assert row.affects_resources is False
        assert row.description and "symmetry" in row.description.lower()
