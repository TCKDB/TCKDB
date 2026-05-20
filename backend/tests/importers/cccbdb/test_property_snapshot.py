"""Snapshot integration tests for the ``experimental-properties`` pilot.

Verifies the runner dispatches the property-table parser, archives
raw HTML + parsed JSON under the right filename prefix, and writes a
manifest with ``page_kind=experimental_property_table``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.importers.cccbdb.crawl_plan import (
    EXPERIMENTAL_PROPERTIES_PILOT,
    CrawlTarget,
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


_FIXTURE_BY_URL = {
    "https://cccbdb.nist.gov/hf0kx.asp": "property_hf_0.html",
    "https://cccbdb.nist.gov/goodlistx.asp": "property_hf_0_with_uncertainty.html",
    "https://cccbdb.nist.gov/diplistx.asp": "property_dipoles.html",
    "https://cccbdb.nist.gov/expdiatomicsx.asp": "property_diatomic_spectroscopic.html",
    # Phase 5c: polarizability_iso added to the pilot.
    "https://cccbdb.nist.gov/pollistx.asp": "property_polarizability_iso.html",
}


@dataclass
class _PropertyFetcher:
    call_counts: dict[str, int]

    @classmethod
    def make(cls) -> "_PropertyFetcher":
        return cls(call_counts={})

    def __call__(self, url: str) -> FetchResult:
        self.call_counts[url] = self.call_counts.get(url, 0) + 1
        if url not in _FIXTURE_BY_URL:
            return FetchResult(None, 404, f"unallowlisted URL {url}")
        text = (FIXTURES_DIR / _FIXTURE_BY_URL[url]).read_text(
            encoding="utf-8"
        )
        return FetchResult(text, 200, None)


def _make_config(tmp_path, fetcher, **overrides):
    return SnapshotConfig(
        output_dir=tmp_path,
        fetcher=fetcher,
        sleep_seconds=0.0,
        **overrides,
    )


class TestPropertyPilotHappyPath:
    def test_writes_property_prefixed_raw_html(self, tmp_path):
        fetcher = _PropertyFetcher.make()
        run_snapshot(
            EXPERIMENTAL_PROPERTIES_PILOT, _make_config(tmp_path, fetcher)
        )
        raw_files = sorted((tmp_path / "raw_html").iterdir())
        assert len(raw_files) == len(EXPERIMENTAL_PROPERTIES_PILOT)
        # Property-table archives use the ``property_<key>_<sha>.html``
        # prefix so a single archive root can mix page kinds without
        # filename collisions.
        for path in raw_files:
            assert path.name.startswith("property_")

    def test_writes_parsed_json_with_property_kind(self, tmp_path):
        fetcher = _PropertyFetcher.make()
        run_snapshot(
            EXPERIMENTAL_PROPERTIES_PILOT, _make_config(tmp_path, fetcher)
        )
        parsed_files = sorted((tmp_path / "parsed").iterdir())
        assert len(parsed_files) == len(EXPERIMENTAL_PROPERTIES_PILOT)
        kinds = {
            json.loads(p.read_text())["source_metadata"]["property_kind"]
            for p in parsed_files
        }
        assert kinds == {
            "hf_0",
            "hf_0_with_uncertainty",
            "dipole",
            "diatomic_spectroscopic",
            "polarizability_iso",
        }

    def test_manifest_records_page_kind(self, tmp_path):
        fetcher = _PropertyFetcher.make()
        run_snapshot(
            EXPERIMENTAL_PROPERTIES_PILOT, _make_config(tmp_path, fetcher)
        )
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        for rec in manifest["records"]:
            assert rec["page_kind"] == "experimental_property_table"
            assert rec["parser_error"] is None
            assert rec["builder_error"] is None

    def test_no_payload_generation_for_property_tables(self, tmp_path):
        """Even with --write-payloads, property-table snapshots stop
        at parsed JSON. molecular_property_observation is still a
        schema gap (Phase 0 spec §7 Gap 1)."""

        fetcher = _PropertyFetcher.make()
        run_snapshot(
            EXPERIMENTAL_PROPERTIES_PILOT,
            _make_config(tmp_path, fetcher, write_payloads=True),
        )
        # No payload files written for any property target.
        assert not (tmp_path / "payloads").exists() or not list(
            (tmp_path / "payloads").iterdir()
        )
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        for rec in manifest["records"]:
            assert rec["payload_json_path"] is None
            assert any(
                "molecular_property_observation" in w
                for w in rec["builder_warnings"]
            )


class TestPropertyCacheRoundTrip:
    def test_second_run_reuses_property_cache(self, tmp_path):
        fetcher = _PropertyFetcher.make()
        run_snapshot(
            EXPERIMENTAL_PROPERTIES_PILOT, _make_config(tmp_path, fetcher)
        )
        first_counts = dict(fetcher.call_counts)
        run_snapshot(
            EXPERIMENTAL_PROPERTIES_PILOT, _make_config(tmp_path, fetcher)
        )
        # Cache served everything; no second fetch.
        assert fetcher.call_counts == first_counts
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        for rec in manifest["records"]:
            assert rec["cache_hit"] is True

    def test_property_cache_does_not_collide_with_species_cache(
        self, tmp_path
    ):
        """A ``property_h2_<sha>.html`` file must not be served as
        an ``experimental_h2`` cache hit (or vice versa)."""

        # Pre-populate a property archive.
        fetcher = _PropertyFetcher.make()
        run_snapshot(
            (EXPERIMENTAL_PROPERTIES_PILOT[0],),
            _make_config(tmp_path, fetcher),
        )
        # Now ask for a species page with species_key="hf_0"; the
        # cache lookup should NOT pick up the property HTML.
        species_target = CrawlTarget(
            species_key="hf_0",  # same key, different page_kind
            source_url="https://cccbdb.nist.gov/exp1x.asp?placeholder",
            page_kind="experimental_species",
        )

        def exploding_fetcher(url):
            raise AssertionError(
                "should not call fetcher in dry-run cold cache"
            )

        manifest = run_snapshot(
            (species_target,),
            _make_config(tmp_path, exploding_fetcher, dry_run=True),
        )
        # Cold-cache dry-run for the species page: fetch warning, not
        # a stolen property cache hit.
        record = manifest["records"][0]
        assert record["cache_hit"] is False
        assert any("dry-run" in w for w in record["fetch_warnings"])


class TestPropertyOnlyDoesNotFetchMoleculePages:
    def test_runner_only_hits_allowlisted_property_urls(self, tmp_path):
        """A property-table run must never accidentally fetch the
        per-species ``exp1x.asp`` endpoints. The fetcher's call_counts
        keys give us exact ground truth."""

        fetcher = _PropertyFetcher.make()
        run_snapshot(
            EXPERIMENTAL_PROPERTIES_PILOT, _make_config(tmp_path, fetcher)
        )
        for url in fetcher.call_counts:
            assert "exp1x.asp" not in url
            assert url in _FIXTURE_BY_URL


class TestPropertyTargetsAreValidatedUrls:
    """Phase 3a's property URLs are empirically verified as flat-table
    single-fetch resources (WebFetch survey, May 2026). They should
    NOT be gated behind ``--allow-unverified-urls``."""

    def test_all_property_targets_validated(self):
        for target in EXPERIMENTAL_PROPERTIES_PILOT:
            assert target.is_validated_url, (
                f"{target.species_key}: property-table URLs are "
                "verified flat-fetch resources"
            )

    @pytest.mark.parametrize(
        "target", EXPERIMENTAL_PROPERTIES_PILOT, ids=lambda t: t.species_key
    )
    def test_property_kind_present_for_property_table_targets(self, target):
        assert target.page_kind == "experimental_property_table"
        assert target.property_kind, "property_kind required on this page kind"
