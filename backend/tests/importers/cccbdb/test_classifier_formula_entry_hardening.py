"""Regression tests for the Phase 5b classifier hardening.

The CCCBDB formula-entry page carries a deceptive
``<TITLE>CCCBDB All data for one molecule</TITLE>`` and an H1 of
``All data (experiment and calculated) for one species``. The
Phase 5a classifier accepted such pages as ``molecule_data_page``
because the molecule-data heading + the bare word "CAS" anywhere
in the body was enough. This file pins the new precedence and the
strict identifier-value requirements.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.importers.cccbdb.crawl_plan import CrawlTarget
from app.importers.cccbdb.diagnostics import (
    Classification,
    classify_html,
)
from app.importers.cccbdb.snapshot import (
    FetchResult,
    SnapshotConfig,
    run_snapshot,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)

_FORMULA_ENTRY_LIVE = (
    FIXTURES_DIR / "species_alldata_formula_entry_live.html"
).read_text(encoding="utf-8")

_REAL_DATA_URL = "https://cccbdb.nist.gov/alldata2x.asp?casno=7732185"
_BAD_CAS_URL = "https://cccbdb.nist.gov/alldata2x.asp?casno=12385136"
_EXP1X = "https://cccbdb.nist.gov/exp1x.asp"


# ---------------------------------------------------------------------------
# Classifier behavior
# ---------------------------------------------------------------------------


class TestFormulaEntryOutranksMoleculeData:
    """The deceptive ``All data for one molecule`` title must NOT
    promote a form page to ``molecule_data_page``."""

    def test_formula_entry_when_url_unchanged(self):
        result = classify_html(
            _FORMULA_ENTRY_LIVE,
            attempted_url=_EXP1X,
            final_url=_EXP1X,
        )
        assert result.classification == Classification.formula_entry_page
        assert (
            "select a species by entering a chemical formula" in result.reason
        )

    def test_redirect_landing_when_url_changed(self):
        result = classify_html(
            _FORMULA_ENTRY_LIVE,
            attempted_url=_BAD_CAS_URL,
            final_url=_EXP1X,
        )
        assert result.classification == Classification.redirect_landing_page
        assert _EXP1X in result.reason

    def test_never_classifies_form_page_as_molecule_data(self):
        """No combination of attempted_url / final_url should ever
        cause this fixture to land as ``molecule_data_page``."""

        for attempted, final in [
            (_REAL_DATA_URL, _REAL_DATA_URL),
            (_REAL_DATA_URL, _EXP1X),
            (_EXP1X, _EXP1X),
            (_EXP1X, None),
        ]:
            result = classify_html(
                _FORMULA_ENTRY_LIVE,
                attempted_url=attempted,
                final_url=final,
            )
            assert result.classification != Classification.molecule_data_page, (
                f"got {result.classification.value} for "
                f"attempted={attempted!r} final={final!r}"
            )


class TestMoleculeDataRequiresPopulatedIdentifier:
    """A page with only nav/menu links to identifier *concepts*
    (the word "CAS" with no real CAS number, the word "InChIKey"
    with no actual 27-char hash, etc.) must NOT classify as
    molecule data."""

    def test_concept_only_page_is_not_molecule_data(self):
        html = """
        <html><head><title>All data for one species</title></head><body>
        <h1>All data for one species</h1>
        <ul>
          <li>CAS Registry Number</li>
          <li>InChI string</li>
          <li>InChIKey hash</li>
          <li>SMILES notation</li>
        </ul>
        <p>No values here, just identifier concepts.</p>
        </body></html>
        """
        result = classify_html(html, attempted_url="x", final_url="x")
        assert result.classification != Classification.molecule_data_page

    def test_h1_and_title_alone_do_not_count_as_molecule_data(self):
        """No body content beyond the heading. Should fall through."""

        html = """
        <html><head><title>All data for one molecule</title></head><body>
        <h1>All data for one species</h1>
        </body></html>
        """
        result = classify_html(html, attempted_url="x", final_url="x")
        # No populated identifier → not molecule_data. Should land as
        # ``unknown`` because there are no other markers.
        assert result.classification != Classification.molecule_data_page
        assert result.classification == Classification.unknown

    def test_navigation_property_links_do_not_count_as_data(self):
        """Pages that list links to property tables (the form-entry
        page has these) must not be promoted to molecule_data."""

        html = """
        <html><head><title>All data for one species</title></head><body>
        <h1>All data for one species</h1>
        <a href="hf0kx.asp">Enthalpy of formation</a>
        <a href="expvibs1x.asp">Vibrations</a>
        <a href="inchix.asp">InChI catalog</a>
        </body></html>
        """
        result = classify_html(html, attempted_url="x", final_url="x")
        assert result.classification != Classification.molecule_data_page


class TestRealMoleculeDataStillAccepted:
    """Hardening must not break the happy path — a real per-species
    page with an InChI value still classifies as molecule_data."""

    def test_h2o_with_real_inchi(self):
        html = """
        <html><head><title>All data for one species: Water</title></head><body>
        <h1>All data for one species</h1>
        <table><tr><th>InChI</th><td>InChI=1S/H2O/h1H2</td></tr></table>
        </body></html>
        """
        result = classify_html(
            html, attempted_url=_REAL_DATA_URL, final_url=_REAL_DATA_URL
        )
        assert result.classification == Classification.molecule_data_page

    def test_real_cas_number_pattern_counts(self):
        html = """
        <html><head><title>All data for one species: Water</title></head><body>
        <h1>All data for one species</h1>
        <p>CAS Registry Number: 7732-18-5</p>
        </body></html>
        """
        result = classify_html(html, attempted_url="x", final_url="x")
        assert result.classification == Classification.molecule_data_page

    def test_real_inchikey_pattern_counts(self):
        html = """
        <html><head><title>All data for one species</title></head><body>
        <h1>All data for one species</h1>
        <p>InChIKey: XLYOFNOQVPJJNP-UHFFFAOYSA-N</p>
        </body></html>
        """
        result = classify_html(html, attempted_url="x", final_url="x")
        assert result.classification == Classification.molecule_data_page


# ---------------------------------------------------------------------------
# Snapshot-runner regression
# ---------------------------------------------------------------------------


@dataclass
class _FormFetcher:
    """Returns the live-shape formula-entry HTML for any URL."""

    final_url: str | None

    def __call__(self, url: str) -> FetchResult:
        return FetchResult(
            text=_FORMULA_ENTRY_LIVE,
            http_status=200,
            error=None,
            final_url=self.final_url,
        )


def _species_target(species_key: str = "benzene") -> CrawlTarget:
    return CrawlTarget(
        species_key=species_key,
        source_url=_REAL_DATA_URL,
        page_kind="species_all_data",
        is_validated_url=True,
        cas_number="71432",
    )


def _make_config(tmp_path, fetcher, **overrides) -> SnapshotConfig:
    return SnapshotConfig(
        output_dir=tmp_path,
        fetcher=fetcher,
        sleep_seconds=0.0,
        **overrides,
    )


class TestSnapshotRejectsFormulaEntryAsSpeciesData:
    """The headline regression case: a direct-CAS fetch that returns
    the deceptive formula-entry HTML must be rejected by the gate."""

    def test_accepted_as_data_is_false(self, tmp_path):
        # final_url == attempted_url → classification = formula_entry_page
        fetcher = _FormFetcher(final_url=_REAL_DATA_URL)
        manifest = run_snapshot(
            (_species_target(),),
            _make_config(tmp_path, fetcher),
        )
        rec = manifest["records"][0]
        assert rec["accepted_as_data"] is False
        assert rec["classification"] == "formula_entry_page"
        assert rec["raw_html_path"] is None
        assert rec["parsed_json_path"] is None

    def test_redirect_case_also_rejected(self, tmp_path):
        # final_url != attempted_url → classification = redirect_landing_page
        fetcher = _FormFetcher(final_url=_EXP1X)
        manifest = run_snapshot(
            (_species_target(),),
            _make_config(tmp_path, fetcher),
        )
        rec = manifest["records"][0]
        assert rec["accepted_as_data"] is False
        assert rec["classification"] == "redirect_landing_page"
        assert rec["raw_html_path"] is None
        assert rec["parsed_json_path"] is None

    def test_rejected_html_written_only_with_flag(self, tmp_path):
        fetcher = _FormFetcher(final_url=_REAL_DATA_URL)
        run_snapshot(
            (_species_target(),),
            _make_config(tmp_path, fetcher, save_rejected_html=True),
        )
        rejected_files = sorted((tmp_path / "rejected_html").iterdir())
        assert len(rejected_files) == 1
        assert rejected_files[0].name.startswith("species_alldata_benzene_")
        rec = json.loads((tmp_path / "manifest.json").read_text())[
            "records"
        ][0]
        assert rec["rejected_html_path"]

    def test_parser_not_invoked_on_rejected_form_page(self, tmp_path):
        """If the parser ran, parsed/ would contain a JSON dump. The
        gate must short-circuit before parser invocation."""

        fetcher = _FormFetcher(final_url=_REAL_DATA_URL)
        run_snapshot(
            (_species_target(),),
            _make_config(tmp_path, fetcher),
        )
        parsed_dir = tmp_path / "parsed"
        if parsed_dir.exists():
            assert list(parsed_dir.iterdir()) == []
