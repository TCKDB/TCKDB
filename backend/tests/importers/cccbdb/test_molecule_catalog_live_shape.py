"""Regression tests for the live ``inchix.asp`` table shape.

The pre-Phase-11 parser silently dropped the entire catalog table
when CCCBDB's ASP backend timed out mid-response (no closing
``</table>``). These tests pin the close-time flush + new live
column shape (``casno | formula | name | InChI | InChIkey | SMILES
| sketch | other names``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.importers.cccbdb.parsers.molecule_catalog import (
    parse_molecule_catalog_page,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)
LIVE_FIXTURE = FIXTURES_DIR / "catalog_inchix_live.html"


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def catalog():
    return parse_molecule_catalog_page(
        LIVE_FIXTURE.read_text(encoding="utf-8"),
        source_url="https://cccbdb.nist.gov/inchix.asp",
    )


def test_live_shaped_inchix_parses_nonzero_entries(catalog):
    """Pre-fix: parser dropped the entire table because of the
    missing ``</table>`` after the ASP timeout. Post-fix: the
    close-time flush captures every row before the truncation
    point."""

    assert len(catalog.entries) > 0


def test_live_headers_are_matched_case_insensitively(catalog):
    """``casno`` / ``InChIkey`` / ``InChI`` should all resolve to
    their canonical model fields regardless of CCCBDB's case."""

    headers = catalog.column_names
    assert "casno" in headers
    assert "formula" in headers
    assert "InChIkey" in headers  # raw header preserves case
    # At least one entry must carry each mapped identifier — proving
    # the case-insensitive alias resolution actually fired.
    assert any(e.cas_number for e in catalog.entries)
    assert any(e.inchikey for e in catalog.entries)


def test_water_row_parses_correctly(catalog):
    water = next(e for e in catalog.entries if e.name == "Water")
    assert water.formula == "H2O"
    assert water.cas_number == "7732185"
    assert water.inchi == "InChI=1S/H2O/h1H2"
    assert water.inchikey == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
    assert water.smiles == "O"
    assert water.raw_href == "alldata2x.asp?casno=7732185"
    assert "Dihydrogen oxide" in water.other_names
    assert "oxidane" in water.other_names


def test_methane_row_parses_correctly(catalog):
    methane = next(e for e in catalog.entries if e.name == "Methane")
    assert methane.formula == "CH4"
    assert methane.cas_number == "74828"
    assert methane.inchikey == "VNWKTOKETHGBQD-UHFFFAOYSA-N"
    assert methane.smiles == "C"
    assert "Marsh gas" in methane.other_names


# ---------------------------------------------------------------------------
# Formula sub/sup normalization
# ---------------------------------------------------------------------------


class TestFormulaNormalization:
    def test_subscript_inlined_to_digit(self, catalog):
        d2o = next(e for e in catalog.entries if e.name == "Heavy water")
        assert d2o.formula == "D2O"

    def test_anion_preserved(self, catalog):
        anion = next(
            e for e in catalog.entries if e.name == "Hydrogen atom anion"
        )
        assert anion.formula == "H-"

    def test_cation_preserved(self, catalog):
        cation = next(
            e for e in catalog.entries if e.name == "Hydrogen atom cation"
        )
        assert cation.formula == "H+"

    def test_combined_sub_and_sup(self, catalog):
        """``CH<sub>3</sub><sup>-</sup>`` → ``CH3-``."""
        methyl = next(
            e for e in catalog.entries if e.name == "Methyl anion"
        )
        assert methyl.formula == "CH3-"

    def test_water_cation(self, catalog):
        """``H<sub>2</sub>O<sup>+</sup>`` → ``H2O+``."""
        water_p = next(
            e for e in catalog.entries if e.name == "Water cation"
        )
        assert water_p.formula == "H2O+"

    def test_deuterium_preserved(self, catalog):
        d = next(e for e in catalog.entries if e.name == "Deuterium atom")
        assert d.formula == "D"
        d2 = next(
            e for e in catalog.entries if e.name == "Deuterium diatomic"
        )
        assert d2.formula == "D2"


# ---------------------------------------------------------------------------
# raw_href provenance + safety
# ---------------------------------------------------------------------------


class TestRawHrefSafety:
    def test_name_link_href_preserved_verbatim(self, catalog):
        for entry in catalog.entries:
            if entry.raw_href is not None:
                # The href is preserved verbatim; we only audit-check
                # that the path is the relative ASP form CCCBDB uses.
                assert entry.raw_href.startswith("alldata2x.asp?")

    def test_raw_href_never_promoted_to_trusted_url(self, catalog):
        for entry in catalog.entries:
            assert entry.trusted_property_url is None
            assert entry.trusted_species_url is None


# ---------------------------------------------------------------------------
# Tolerance — missing fields don't crash
# ---------------------------------------------------------------------------


class TestParserTolerance:
    def test_row_with_empty_inchikey_still_parsed(self, catalog):
        """Water cation in the fixture has an empty ``<td></td>`` for
        InChIKey. The row must still appear in the catalog with the
        other identifiers populated."""

        water_p = next(
            e for e in catalog.entries if e.name == "Water cation"
        )
        assert water_p.formula == "H2O+"
        assert water_p.inchikey is None
        assert water_p.cas_number == "14531534"

    def test_duplicate_formula_distinct_isomers_both_present(self, catalog):
        """Ethanol and dimethyl ether share formula C2H6O but are
        distinct catalog entries."""

        c2h6o = [e for e in catalog.entries if e.formula == "C2H6O"]
        names = {e.name for e in c2h6o}
        assert "Ethanol" in names
        assert "Dimethyl ether" in names

    def test_truncated_trailing_row_still_visible(self, catalog):
        """The fixture's last row has no closing ``</tr>``/``</table>`` —
        the parser's close-time flush must still surface it (even if
        some of its later cells got merged into the cell that was
        in-flight when the ASP error block fired)."""

        truncated = next(
            (e for e in catalog.entries if e.name == "Truncated row"),
            None,
        )
        assert truncated is not None
        assert truncated.formula == "X"
        assert truncated.cas_number == "99999999"


# ---------------------------------------------------------------------------
# Queue-generator regression
# ---------------------------------------------------------------------------


def test_queue_generator_consumes_parsed_catalog_and_writes_nonzero(
    tmp_path, catalog
):
    """End-to-end: parsed catalog JSON feeds the queue generator and
    yields a non-empty queue. The pre-fix path silently produced 0
    queue records because the parsed catalog itself was empty."""

    from scripts.cccbdb_generate_form_queue import (
        load_catalog_entries,
        generate_queue,
        write_queue_file,
        QueueGenFilters,
    )

    catalog_json = tmp_path / "catalog.json"
    catalog_json.write_text(catalog.model_dump_json())

    entries = load_catalog_entries(catalog_json)
    assert len(entries) > 0  # the bug we're regressing

    result = generate_queue(
        entries,
        target_kind="atomization_energy",
        entry_url="https://cccbdb.nist.gov/ea1x.asp",
        filters=QueueGenFilters(limit=5),
    )
    assert result.written == 5

    out = tmp_path / "form_queue.json"
    write_queue_file(result, out)
    data = json.loads(out.read_text())
    assert len(data["records"]) == 5


def test_require_inchikey_skips_only_rows_without_inchikey(
    tmp_path, catalog
):
    """``--require-inchikey`` must skip exactly the rows whose
    InChIKey cell was blank — and ONLY those rows. Water (with an
    InChIKey) should make it through; Water cation (without) should
    not."""

    from scripts.cccbdb_generate_form_queue import (
        load_catalog_entries,
        generate_queue,
        QueueGenFilters,
    )

    catalog_json = tmp_path / "catalog.json"
    catalog_json.write_text(catalog.model_dump_json())
    entries = load_catalog_entries(catalog_json)

    result = generate_queue(
        entries,
        target_kind="atomization_energy",
        entry_url="https://cccbdb.nist.gov/ea1x.asp",
        filters=QueueGenFilters(require_inchikey=True),
    )
    names = {r.name for r in result.records}
    assert "Water" in names
    assert "Water cation" not in names


def test_queue_records_do_not_use_raw_href_as_entry_url(
    tmp_path, catalog
):
    """A safety regression: ``inchix.asp`` ``raw_href`` values
    (``alldata2x.asp?casno=...``) must NEVER end up as the queue
    record's ``entry_url``. The CLI flag value is the only URL the
    queue ever points at."""

    from scripts.cccbdb_generate_form_queue import (
        load_catalog_entries,
        generate_queue,
        write_queue_file,
        QueueGenFilters,
    )

    catalog_json = tmp_path / "catalog.json"
    catalog_json.write_text(catalog.model_dump_json())
    entries = load_catalog_entries(catalog_json)

    result = generate_queue(
        entries,
        target_kind="atomization_energy",
        entry_url="https://cccbdb.nist.gov/ea1x.asp",
        filters=QueueGenFilters(limit=10),
    )
    out = tmp_path / "form_queue.json"
    write_queue_file(result, out)

    data = json.loads(out.read_text())
    for rec in data["records"]:
        assert rec["entry_url"] == "https://cccbdb.nist.gov/ea1x.asp"
        assert "alldata2x.asp" not in rec["entry_url"]
        assert "raw_href" not in rec
