"""Snapshot + parser tests for the Phase 5a direct-CAS resolver.

Covers the classification gate (the headline behavior), filename
prefix isolation, rejected_html archiving, and the minimal parser's
identifier extraction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.importers.cccbdb.crawl_plan import (
    SPECIES_ALLDATA_CAS_PILOT,
    CrawlTarget,
)
from app.importers.cccbdb.parsers import parse_species_all_data_page
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

_H2O_URL = "https://cccbdb.nist.gov/alldata2x.asp?casno=7732185"
_REDIRECT_URL = "https://cccbdb.nist.gov/alldata2x.asp?casno=12385136"
_EXP1X = "https://cccbdb.nist.gov/exp1x.asp"

_FORMULA_ENTRY_HTML = (
    FIXTURES_DIR / "species_alldata_redirect_landing.html"
).read_text(encoding="utf-8")
_H2O_HTML = (FIXTURES_DIR / "species_alldata_h2o.html").read_text(encoding="utf-8")
_CLOUDFLARE_HTML = (
    "<html><body>You are being rate limited. "
    "Error 1015 Cloudflare.</body></html>"
)
_UNKNOWN_HTML = "<html><body>Random page with no recognizable markers.</body></html>"


@dataclass
class _SpeciesFetcher:
    """Maps URLs to (html, final_url) and records call counts."""

    responses: dict[str, tuple[str, str | None]]
    calls: list[str]

    @classmethod
    def make(cls, responses: dict[str, tuple[str, str | None]]) -> "_SpeciesFetcher":
        return cls(responses=responses, calls=[])

    def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        if url not in self.responses:
            return FetchResult(None, 404, f"unallowlisted URL {url}")
        text, final_url = self.responses[url]
        return FetchResult(text=text, http_status=200, error=None, final_url=final_url)


def _make_config(tmp_path: Path, fetcher, **overrides) -> SnapshotConfig:
    return SnapshotConfig(
        output_dir=tmp_path,
        fetcher=fetcher,
        sleep_seconds=0.0,
        **overrides,
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestSpeciesAllDataParser:
    def test_extracts_all_five_identifiers(self):
        rec = parse_species_all_data_page(
            _H2O_HTML,
            source_url=_H2O_URL,
            cas_number="7732185",
            source_record_key="h2o",
        )
        assert rec.title == "All data for one species: H2O"
        assert rec.detected_name == "Water"
        assert rec.detected_formula == "H2O"
        assert rec.detected_inchi == "InChI=1S/H2O/h1H2"
        assert rec.detected_inchikey == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        assert rec.detected_smiles == "O"

    def test_section_headings_ordered(self):
        rec = parse_species_all_data_page(
            _H2O_HTML, source_url=_H2O_URL, cas_number="7732185"
        )
        assert rec.section_headings[0] == "All data for one species"
        assert "Identifiers" in rec.section_headings
        assert "Experimental thermochemistry" in rec.section_headings

    def test_cas_number_preserved_in_source_metadata(self):
        rec = parse_species_all_data_page(
            _H2O_HTML, source_url=_H2O_URL, cas_number="7732185"
        )
        assert rec.source_metadata.cas_number == "7732185"
        assert rec.source_metadata.page_kind == "species_all_data"

    def test_empty_source_url_rejected(self):
        with pytest.raises(ValueError):
            parse_species_all_data_page(_H2O_HTML, source_url="")

    def test_empty_html_warns(self):
        rec = parse_species_all_data_page(
            "<html></html>",
            source_url="https://example.invalid/empty",
        )
        assert rec.detected_inchi is None
        assert any("no headings" in w for w in rec.warnings)


# ---------------------------------------------------------------------------
# Classification gate — accept paths
# ---------------------------------------------------------------------------


class TestGateAcceptsMoleculeDataPage:
    def test_writes_species_alldata_prefixed_raw_html(self, tmp_path):
        fetcher = _SpeciesFetcher.make({_H2O_URL: (_H2O_HTML, _H2O_URL)})
        target = SPECIES_ALLDATA_CAS_PILOT[0]  # h2o
        run_snapshot((target,), _make_config(tmp_path, fetcher))
        raw_files = sorted((tmp_path / "raw_html").iterdir())
        assert len(raw_files) == 1
        assert raw_files[0].name.startswith("species_alldata_h2o_")
        sha = hashlib.sha256(raw_files[0].read_bytes()).hexdigest()
        # Filename ends with first 12 chars of sha.
        assert raw_files[0].stem.split("_")[-1] == sha[:12]

    def test_manifest_records_accepted_with_classification(self, tmp_path):
        fetcher = _SpeciesFetcher.make({_H2O_URL: (_H2O_HTML, _H2O_URL)})
        target = SPECIES_ALLDATA_CAS_PILOT[0]
        run_snapshot((target,), _make_config(tmp_path, fetcher))
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        rec = manifest["records"][0]
        assert rec["page_kind"] == "species_all_data"
        assert rec["resolver_strategy"] == "direct_alldata2x_casno"
        assert rec["classification"] == "molecule_data_page"
        assert rec["accepted_as_data"] is True
        assert rec["raw_html_path"]
        assert rec["rejected_html_path"] is None
        assert rec["final_url"] == _H2O_URL
        assert rec["parsed_json_path"]

    def test_parser_runs_on_accepted_page(self, tmp_path):
        fetcher = _SpeciesFetcher.make({_H2O_URL: (_H2O_HTML, _H2O_URL)})
        target = SPECIES_ALLDATA_CAS_PILOT[0]
        run_snapshot((target,), _make_config(tmp_path, fetcher))
        parsed = json.loads(
            next((tmp_path / "parsed").iterdir()).read_text()
        )
        # The parsed JSON carries the minimal triage fields.
        assert parsed["detected_inchikey"] == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        assert (
            parsed["source_metadata"]["page_kind"] == "species_all_data"
        )


# ---------------------------------------------------------------------------
# Classification gate — reject paths
# ---------------------------------------------------------------------------


class TestGateRejectsRedirectLanding:
    """The headline diagnostic case: ``alldata2x.asp?casno=12385136``
    302s to the formula-entry form. The gate must reject."""

    def _make_target(self) -> CrawlTarget:
        return CrawlTarget(
            species_key="bad",
            source_url=_REDIRECT_URL,
            page_kind="species_all_data",
            is_validated_url=True,
            cas_number="12385136",
        )

    def test_classification_is_redirect_landing(self, tmp_path):
        fetcher = _SpeciesFetcher.make(
            {_REDIRECT_URL: (_FORMULA_ENTRY_HTML, _EXP1X)}
        )
        run_snapshot((self._make_target(),), _make_config(tmp_path, fetcher))
        rec = json.loads((tmp_path / "manifest.json").read_text())[
            "records"
        ][0]
        assert rec["classification"] == "redirect_landing_page"
        assert rec["accepted_as_data"] is False
        assert rec["final_url"] == _EXP1X
        assert any(
            "rejected by classification gate" in w
            for w in rec["resolver_warnings"]
        )

    def test_raw_html_not_written_on_reject(self, tmp_path):
        fetcher = _SpeciesFetcher.make(
            {_REDIRECT_URL: (_FORMULA_ENTRY_HTML, _EXP1X)}
        )
        run_snapshot((self._make_target(),), _make_config(tmp_path, fetcher))
        raw_dir = tmp_path / "raw_html"
        # raw_html/ exists but contains nothing for this target.
        assert list(raw_dir.iterdir()) == []
        rec = json.loads((tmp_path / "manifest.json").read_text())[
            "records"
        ][0]
        assert rec["raw_html_path"] is None
        # Parser is not invoked on rejected pages.
        assert rec["parsed_json_path"] is None

    def test_save_rejected_html_writes_rejected_dir(self, tmp_path):
        fetcher = _SpeciesFetcher.make(
            {_REDIRECT_URL: (_FORMULA_ENTRY_HTML, _EXP1X)}
        )
        run_snapshot(
            (self._make_target(),),
            _make_config(tmp_path, fetcher, save_rejected_html=True),
        )
        rejected = sorted((tmp_path / "rejected_html").iterdir())
        assert len(rejected) == 1
        assert rejected[0].name.startswith("species_alldata_bad_")
        rec = json.loads((tmp_path / "manifest.json").read_text())[
            "records"
        ][0]
        assert rec["rejected_html_path"]
        assert rec["rejected_html_path"].startswith("rejected_html/")
        # raw_html_path stays null even when rejected HTML is saved.
        assert rec["raw_html_path"] is None


class TestGateRejectsRateLimit:
    def test_cloudflare_1015_body_rejected(self, tmp_path):
        target = CrawlTarget(
            species_key="ratelim",
            source_url=_H2O_URL,
            page_kind="species_all_data",
            is_validated_url=True,
            cas_number="7732185",
        )
        fetcher = _SpeciesFetcher.make({_H2O_URL: (_CLOUDFLARE_HTML, _H2O_URL)})
        run_snapshot((target,), _make_config(tmp_path, fetcher))
        rec = json.loads((tmp_path / "manifest.json").read_text())[
            "records"
        ][0]
        assert rec["classification"] == "rate_limit_or_error_page"
        assert rec["accepted_as_data"] is False
        assert rec["raw_html_path"] is None


class TestGateRejectsUnknown:
    def test_unknown_page_rejected_by_default(self, tmp_path):
        target = CrawlTarget(
            species_key="unknown",
            source_url=_H2O_URL,
            page_kind="species_all_data",
            is_validated_url=True,
            cas_number="7732185",
        )
        fetcher = _SpeciesFetcher.make({_H2O_URL: (_UNKNOWN_HTML, _H2O_URL)})
        run_snapshot((target,), _make_config(tmp_path, fetcher))
        rec = json.loads((tmp_path / "manifest.json").read_text())[
            "records"
        ][0]
        assert rec["classification"] == "unknown"
        assert rec["accepted_as_data"] is False
        assert rec["raw_html_path"] is None


# ---------------------------------------------------------------------------
# Cache isolation
# ---------------------------------------------------------------------------


class TestSpeciesAllDataCacheIsolation:
    def test_does_not_share_cache_with_experimental_species(self, tmp_path):
        """An accepted species_all_data run must not serve as a cache
        hit for an experimental_species target sharing the species_key."""

        fetcher = _SpeciesFetcher.make({_H2O_URL: (_H2O_HTML, _H2O_URL)})
        run_snapshot(
            (SPECIES_ALLDATA_CAS_PILOT[0],),  # h2o
            _make_config(tmp_path, fetcher),
        )
        # Now ask for an experimental_species target with the same key.
        species_target = CrawlTarget(
            species_key="h2o",
            source_url="https://cccbdb.nist.gov/exp1x.asp?placeholder",
            page_kind="experimental_species",
        )

        def exploding(url):
            raise AssertionError("must not fetch — testing cache miss path")

        manifest = run_snapshot(
            (species_target,),
            _make_config(tmp_path, exploding, dry_run=True),
        )
        assert manifest["records"][0]["cache_hit"] is False

    def test_does_not_share_cache_with_catalog(self, tmp_path):
        fetcher = _SpeciesFetcher.make({_H2O_URL: (_H2O_HTML, _H2O_URL)})
        run_snapshot(
            (SPECIES_ALLDATA_CAS_PILOT[0],),
            _make_config(tmp_path, fetcher),
        )
        catalog_target = CrawlTarget(
            species_key="h2o",  # same key, different page_kind
            source_url="https://cccbdb.nist.gov/inchix.asp",
            page_kind="molecule_catalog_inchi_index",
            is_validated_url=True,
        )

        def exploding(url):
            raise AssertionError("must not fetch — testing cache miss path")

        manifest = run_snapshot(
            (catalog_target,),
            _make_config(tmp_path, exploding, dry_run=True),
        )
        assert manifest["records"][0]["cache_hit"] is False

    def test_does_not_share_cache_with_property_table(self, tmp_path):
        fetcher = _SpeciesFetcher.make({_H2O_URL: (_H2O_HTML, _H2O_URL)})
        run_snapshot(
            (SPECIES_ALLDATA_CAS_PILOT[0],),
            _make_config(tmp_path, fetcher),
        )
        property_target = CrawlTarget(
            species_key="h2o",
            source_url="https://cccbdb.nist.gov/hf0kx.asp",
            page_kind="experimental_property_table",
            property_kind="hf_0",
            is_validated_url=True,
        )

        def exploding(url):
            raise AssertionError("must not fetch — testing cache miss path")

        manifest = run_snapshot(
            (property_target,),
            _make_config(tmp_path, exploding, dry_run=True),
        )
        assert manifest["records"][0]["cache_hit"] is False


# ---------------------------------------------------------------------------
# Policy contract: raw_href from catalog is never used as a species URL
# ---------------------------------------------------------------------------


class TestRawHrefNeverUsed:
    def test_only_alldata2x_urls_attempted_for_this_pilot(self, tmp_path):
        """The Phase 5a pilot's allowlist must use ``alldata2x.asp``
        directly, NOT some construction from ``inchix.asp`` hrefs."""

        for target in SPECIES_ALLDATA_CAS_PILOT:
            assert target.source_url.startswith(
                "https://cccbdb.nist.gov/alldata2x.asp"
            )
            assert target.cas_number


# ---------------------------------------------------------------------------
# Pilot allowlist invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    SPECIES_ALLDATA_CAS_PILOT,
    ids=lambda t: t.species_key,
)
def test_pilot_targets_typed_for_species_alldata(target):
    assert target.page_kind == "species_all_data"
    assert target.cas_number is not None
    assert target.is_validated_url is True
    # The URL is built from the CAS, not from a catalog raw_href.
    assert f"casno={target.cas_number}" in target.source_url
