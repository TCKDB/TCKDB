"""Parser tests for the CCCBDB cross-species property-table parser.

All tests are fully offline and consume the bundled HTML fixtures
under ``backend/app/importers/cccbdb/fixtures/property_*.html``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.parsers import (
    PROPERTY_CONFIGS,
    parse_experimental_property_table_page,
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
# Hf(0K) — value + reference + DOI
# ---------------------------------------------------------------------------


class TestHfZeroKTable:
    @pytest.fixture(scope="class")
    def table(self):
        return parse_experimental_property_table_page(
            _load("property_hf_0.html"),
            property_kind="hf_0",
            source_url="https://cccbdb.nist.gov/hf0kx.asp",
        )

    def test_title_and_units(self, table):
        assert "enthalpy of formation at 0k" in (table.title or "").lower()
        assert table.raw_units == "kJ mol^-1"
        assert table.canonical_unit == "kJ/mol"

    def test_column_names_match_real_page(self, table):
        # Quoted from the live page; the parser must not silently
        # drop or rename columns.
        assert table.column_names == [
            "Species",
            "Name",
            "Hfg 0K",
            "Reference",
            "DOI",
        ]

    def test_row_count(self, table):
        assert len(table.rows) == 6

    def test_first_row_values(self, table):
        row = table.rows[0]
        assert row.row_index == 0
        assert row.formula == "D"
        assert row.name == "Deuterium atom"
        assert row.value == pytest.approx(219.8)
        assert row.unit == "kJ mol^-1"
        assert row.normalized_value == pytest.approx(219.8)
        assert row.normalized_unit == "kJ/mol"
        assert row.reference is not None
        assert row.reference.reference_label == "Gurvich"

    def test_h_row_has_doi(self, table):
        row = next(r for r in table.rows if r.formula == "H")
        assert row.reference is not None
        # DOI rides along as parsed_literature_hint.
        assert (
            row.reference.parsed_literature_hint
            == "10.1002/bbpc.19900940121"
        )
        assert row.reference.reference_label == "CODATA"

    def test_no_uncertainty_on_this_table(self, table):
        for row in table.rows:
            assert row.uncertainty is None

    def test_provenance(self, table):
        meta = table.source_metadata
        assert meta.source == SOURCE_NAME
        assert meta.source_release == SOURCE_RELEASE
        assert meta.source_database_doi == SOURCE_DATABASE_DOI
        assert meta.page_kind == "experimental_property_table"
        assert meta.property_kind == "hf_0"
        assert meta.parser_version == PARSER_VERSION
        assert len(meta.content_sha256) == 64


# ---------------------------------------------------------------------------
# Hf(0K) with uncertainty — value AND uncertainty share the same unit
# ---------------------------------------------------------------------------


class TestGoodlistTable:
    @pytest.fixture(scope="class")
    def table(self):
        return parse_experimental_property_table_page(
            _load("property_hf_0_with_uncertainty.html"),
            property_kind="hf_0_with_uncertainty",
            source_url="https://cccbdb.nist.gov/goodlistx.asp",
        )

    def test_units(self, table):
        assert table.canonical_unit == "kJ/mol"

    def test_lih_value_and_uncertainty_converted_together(self, table):
        row = next(r for r in table.rows if r.formula == "LiH")
        assert row.value == pytest.approx(140.804)
        assert row.normalized_value == pytest.approx(140.804)
        assert row.uncertainty == pytest.approx(0.040)
        assert row.normalized_uncertainty == pytest.approx(0.040)

    def test_co2_negative_value_preserved(self, table):
        row = next(r for r in table.rows if r.formula == "CO2")
        assert row.value == pytest.approx(-393.145)
        assert row.normalized_value == pytest.approx(-393.145)


# ---------------------------------------------------------------------------
# Dipoles — non-normalizable unit (Debye); reference + comment per row
# ---------------------------------------------------------------------------


class TestDipoleTable:
    @pytest.fixture(scope="class")
    def table(self):
        return parse_experimental_property_table_page(
            _load("property_dipoles.html"),
            property_kind="dipole",
            source_url="https://cccbdb.nist.gov/diplistx.asp",
        )

    def test_units_preserved_unchanged(self, table):
        # No Debye normalizer yet — raw unit is preserved as the
        # canonical unit so downstream code can branch on it.
        assert table.raw_units == "Debye"
        assert table.canonical_unit == "Debye"

    def test_tot_column_is_value_column(self, table):
        h2o = next(r for r in table.rows if r.formula == "H2O")
        assert h2o.value == pytest.approx(1.855)
        assert h2o.state_label_raw == "1A1"

    def test_lih_blank_xyz_does_not_crash(self, table):
        # LiH row has blank x/y/z cells but a populated tot; the
        # parser must tolerate this without dropping the row.
        lih = next(r for r in table.rows if r.formula == "LiH")
        assert lih.value == pytest.approx(5.880)
        # The blank x/y/z values still live in raw_row for inspection.
        assert lih.raw_row["x"] == ""
        assert lih.raw_row["y"] == ""
        assert lih.raw_row["z"] == ""

    def test_lih_has_comment(self, table):
        lih = next(r for r in table.rows if r.formula == "LiH")
        assert lih.reference is not None
        assert lih.reference.reference_label == "NSRDS-NBS10"
        assert lih.reference.reference_comment == "MB"

    def test_dipole_raw_row_carries_xyz_components(self, table):
        # x/y/z components are not first-class fields yet; the
        # raw_row dict keeps them addressable.
        h2o = next(r for r in table.rows if r.formula == "H2O")
        assert h2o.raw_row["x"] == "0.000"
        assert h2o.raw_row["z"] == "-1.855"


# ---------------------------------------------------------------------------
# Diatomic spectroscopic — live page has no <th> header row, so the
# parser uses ``configured_column_names`` on PROPERTY_CONFIGS rather
# than inferring from the first row. These tests pin the Phase 5e
# fix against regression to the original bug (row 0 / H2 being eaten
# as the column header).
# ---------------------------------------------------------------------------


class TestDiatomicSpectroscopicTable:
    @pytest.fixture(scope="class")
    def table(self):
        return parse_experimental_property_table_page(
            _load("property_diatomic_spectroscopic.html"),
            property_kind="diatomic_spectroscopic",
            source_url="https://cccbdb.nist.gov/expdiatomicsx.asp",
        )

    def test_units(self, table):
        assert table.canonical_unit == "cm^-1"

    def test_h2_we(self, table):
        row = next(r for r in table.rows if r.formula == "H2")
        assert row.value == pytest.approx(4401.213)
        assert row.normalized_unit == "cm^-1"

    def test_secondary_constants_in_raw_row(self, table):
        # wexe, Be, etc. are not first-class fields; they live in
        # raw_row keyed by the *configured* column name so downstream
        # code can lift them later without re-parsing.
        h2 = next(r for r in table.rows if r.formula == "H2")
        assert h2.raw_row["wexe"] == "121.336"
        assert h2.raw_row["Be"] == "60.853"

    def test_column_names_come_from_configured_tuple_not_first_row(
        self, table
    ):
        """Regression for the Phase 5d bug where the H2 data row was
        eaten as the table header. column_names must match the
        configured tuple, never the values from the first data row."""

        assert table.column_names == [
            "Molecule",
            "name",
            "we",
            "wexe",
            "weye",
            "Be",
            "alpha_e",
            "re",
            "squib",
        ]
        # Specifically: "H2" must NOT appear as a column name.
        assert "H2" not in table.column_names
        # Same for the H2 ωe value, which previously became a column.
        assert "4401.213" not in table.column_names

    def test_first_row_is_h2_data_not_header(self, table):
        """Before the fix, table.rows[0] was D2 (because H2 had been
        consumed as the header). After the fix, row_index 0 is H2."""

        assert table.rows[0].row_index == 0
        h2 = table.rows[0]
        assert h2.formula == "H2"
        assert h2.name == "Hydrogen diatomic"
        assert h2.value == pytest.approx(4401.213)
        assert h2.normalized_value == pytest.approx(4401.213)
        assert h2.unit == "cm^-1"
        assert h2.reference is not None
        assert h2.reference.reference_label == "2007Iri:389"

    def test_h2_plus_cation_row_parses(self, table):
        """The H2+ cation row in the fixture proves the first-row /
        second-row distinction isn't being silently scrambled."""

        h2_plus = next(r for r in table.rows if r.formula == "H2+")
        assert h2_plus.name == "Hydrogen cation"
        assert h2_plus.value == pytest.approx(2321.7)
        assert h2_plus.reference is not None
        assert h2_plus.reference.reference_label == "NSRDS-NBS31"

    def test_raw_row_keys_match_configured_names_not_data_values(
        self, table
    ):
        """raw_row keys are the *configured* column names. The pre-fix
        bug produced keys like ``"4401.213"`` (the H2 we value) — we
        explicitly assert that anti-shape."""

        h2 = table.rows[0]
        # Configured names appear as raw_row keys.
        assert set(h2.raw_row.keys()) == {
            "Molecule",
            "name",
            "we",
            "wexe",
            "weye",
            "Be",
            "alpha_e",
            "re",
            "squib",
        }
        # Data values do NOT appear as raw_row keys.
        for value_key in ("H2", "Hydrogen diatomic", "4401.213"):
            assert value_key not in h2.raw_row

    def test_warning_when_row_cell_count_differs(self):
        """If a row in the table has fewer (or more) cells than the
        configured tuple, the parser emits a single table-level
        warning naming the count. This is a defensive guard against
        future CCCBDB layout drift."""

        # Build a synthetic page with one short row.
        html = """
        <html><body><table>
          <tr><td>H2</td><td>Hydrogen diatomic</td><td>4401.213</td>
              <td>121.336</td><td>0.8129</td><td>60.853</td>
              <td></td><td>3.0622</td><td>2007Iri:389</td></tr>
          <tr><td>X</td><td>shortrow</td><td>1.0</td></tr>
        </table></body></html>
        """
        result = parse_experimental_property_table_page(
            html,
            property_kind="diatomic_spectroscopic",
            source_url="https://example.invalid/diatomic",
        )
        assert any(
            "cell count that does not match" in w for w in result.warnings
        )


# ---------------------------------------------------------------------------
# Cross-cutting behavior
# ---------------------------------------------------------------------------


class TestParserBehavior:
    def test_empty_source_url_rejected(self):
        with pytest.raises(ValueError):
            parse_experimental_property_table_page(
                "<html></html>",
                property_kind="hf_0",
                source_url="",
            )

    def test_unknown_property_kind_rejected(self):
        with pytest.raises(ValueError):
            parse_experimental_property_table_page(
                "<html></html>",
                property_kind="nope_not_a_real_kind",
                source_url="https://example.invalid/",
            )

    def test_empty_page_produces_warning_not_crash(self):
        result = parse_experimental_property_table_page(
            "<html><body></body></html>",
            property_kind="hf_0",
            source_url="https://example.invalid/empty",
        )
        assert result.rows == []
        assert any("no data table" in w for w in result.warnings)

    def test_malformed_value_cell_produces_row_warning(self):
        html = """
        <html><body>
          <h2>Hf(0K)</h2>
          <p>Enthalpies in kJ mol^-1</p>
          <table>
            <tr><th>Species</th><th>Name</th><th>Hfg 0K</th>
                <th>Reference</th><th>DOI</th></tr>
            <tr><td>X</td><td>weird</td><td>not-a-number</td>
                <td>Gurvich</td><td></td></tr>
          </table>
        </body></html>
        """
        result = parse_experimental_property_table_page(
            html,
            property_kind="hf_0",
            source_url="https://example.invalid/malformed",
        )
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.value is None
        assert any("non-numeric" in w for w in row.warnings)
        # Other fields still parse — formula, reference.
        assert row.formula == "X"
        assert row.reference is not None
        assert row.reference.reference_label == "Gurvich"

    def test_property_configs_all_have_value_column_or_tensor_components(self):
        # Sanity: every registered config must EITHER name a scalar
        # ``value_column`` (the workflow-ready case) OR declare a
        # ``tensor_component_columns`` tuple (the tensor-only case,
        # paired with ``workflow_ready=False`` on the CrawlTarget).
        # A config with neither is a maintainer bug.
        for kind, cfg in PROPERTY_CONFIGS.items():
            has_scalar = bool(cfg.value_column)
            has_tensor = bool(cfg.tensor_component_columns)
            assert has_scalar or has_tensor, (
                f"PROPERTY_CONFIGS[{kind!r}] declares neither "
                f"value_column nor tensor_component_columns"
            )

    def test_deterministic_content_sha(self):
        html = _load("property_hf_0.html")
        a = parse_experimental_property_table_page(
            html,
            property_kind="hf_0",
            source_url="https://cccbdb.nist.gov/hf0kx.asp",
        )
        b = parse_experimental_property_table_page(
            html,
            property_kind="hf_0",
            source_url="https://cccbdb.nist.gov/hf0kx.asp",
        )
        assert (
            a.source_metadata.content_sha256
            == b.source_metadata.content_sha256
        )
