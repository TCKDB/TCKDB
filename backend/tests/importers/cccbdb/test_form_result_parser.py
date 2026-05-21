"""Tests for the CCCBDB form-result parser (parsers/form_result.py).

All tests are offline. Fixtures live under
``backend/app/importers/cccbdb/fixtures/form_*``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers.cccbdb.parsers import (
    SUPPORTED_TARGET_KINDS,
    parse_form_result_page,
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
# Atomization energy (the only supported target_kind in Phase 6)
# ---------------------------------------------------------------------------


class TestAtomizationEnergyParse:
    @pytest.fixture(scope="class")
    def parsed(self):
        return parse_form_result_page(
            _load("form_result_ea_h2o.html"),
            target_kind="atomization_energy",
            source_url="https://cccbdb.nist.gov/ea1x.asp",
            final_url="https://cccbdb.nist.gov/ea2x.asp",
        )

    def test_title_and_unit(self, parsed):
        assert "atomization" in (parsed.title or "").lower()
        assert parsed.raw_units == "kJ/mol"

    def test_column_names(self, parsed):
        assert "Species" in parsed.column_names
        assert "0K" in parsed.column_names
        assert "298K" in parsed.column_names

    def test_first_row_extracts_h2o(self, parsed):
        assert len(parsed.rows) == 1
        row = parsed.rows[0]
        # Sub-tag stripped: H<sub>2</sub>O → H2O.
        assert row.formula == "H2O"
        assert row.name == "Water"
        assert row.value == pytest.approx(917.8)
        assert row.unit == "kJ/mol"
        assert row.uncertainty == pytest.approx(0.1)
        assert row.secondary_values["298K"] == pytest.approx(927.0)

    def test_raw_row_preserves_all_columns(self, parsed):
        for row in parsed.rows:
            for col in ("Species", "Name", "0K", "298K", "unc."):
                assert col in row.raw_row, (
                    f"row {row.row_index} dropped column {col!r}"
                )

    def test_no_warnings_on_clean_fixture(self, parsed):
        assert parsed.warnings == []
        for row in parsed.rows:
            assert row.warnings == []


# ---------------------------------------------------------------------------
# Implicit-close cell handling (live HTML uses unclosed <TD>)
# ---------------------------------------------------------------------------


def test_parser_handles_implicit_td_close():
    """Live CCCBDB HTML omits ``</td>`` before the next ``<TD>`` or
    ``</tr>``. The parser must treat a new ``<td>``/``<tr>`` as an
    implicit close. Regression for the May 2026 finding."""

    html = (
        "<html><body>"
        "<H1>Experimental Atomization Energies</H1>"
        "Atomization Energies in kJ mol<sup>-1</sup>"
        "<table>"
        "<TR><TH>Species<TH>Name<TH>0K<TH>298K<TH>unc."
        "<TR><TD>H<sub>2</sub><TD>Hydrogen diatomic"
        "<TD>432.07<TD>435.79<TD>0.001"
        "</table></body></html>"
    )
    parsed = parse_form_result_page(
        html,
        target_kind="atomization_energy",
        source_url="https://cccbdb.nist.gov/ea1x.asp",
        final_url="https://cccbdb.nist.gov/ea2x.asp",
    )
    assert len(parsed.rows) == 1
    assert parsed.rows[0].formula == "H2"
    assert parsed.rows[0].name == "Hydrogen diatomic"
    assert parsed.rows[0].value == pytest.approx(432.07)


# ---------------------------------------------------------------------------
# Unsupported target_kind
# ---------------------------------------------------------------------------


class TestUnsupportedTarget:
    def test_unknown_target_returns_zero_rows_and_warning(self):
        parsed = parse_form_result_page(
            _load("form_result_ea_h2o.html"),
            target_kind="vibrational_frequency",
            source_url="https://cccbdb.nist.gov/expvibs1x.asp",
            final_url="https://cccbdb.nist.gov/vibsx.asp",
        )
        assert parsed.rows == []
        assert any(
            "unsupported target_kind" in w for w in parsed.warnings
        )
        # Title still captured for diagnostic value.
        assert parsed.title is not None
        # Content sha is always populated for archive identity.
        assert parsed.content_sha256 is not None


def test_supported_target_kinds_includes_only_atomization_energy():
    """Phase 6 ships exactly one supported target_kind. New kinds
    should grow this tuple AND add a per-target parser branch."""

    assert "atomization_energy" in SUPPORTED_TARGET_KINDS


# ---------------------------------------------------------------------------
# Missing/degenerate data tables
# ---------------------------------------------------------------------------


def test_missing_table_emits_warning_not_exception():
    parsed = parse_form_result_page(
        "<html><body><h1>Experimental Atomization Energies</h1>"
        "<p>no data here</p></body></html>",
        target_kind="atomization_energy",
        source_url="https://cccbdb.nist.gov/ea1x.asp",
        final_url=None,
    )
    assert parsed.rows == []
    assert any("no atomization-energy table" in w for w in parsed.warnings)
