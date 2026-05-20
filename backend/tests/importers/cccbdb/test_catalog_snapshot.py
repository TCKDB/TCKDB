"""Snapshot integration tests for the ``catalog`` pilot."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.importers.cccbdb.crawl_plan import CATALOG_PILOT, CrawlTarget
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


@dataclass
class _CatalogFetcher:
    call_counts: dict[str, int]

    @classmethod
    def make(cls) -> "_CatalogFetcher":
        return cls(call_counts={})

    def __call__(self, url: str) -> FetchResult:
        self.call_counts[url] = self.call_counts.get(url, 0) + 1
        if url != "https://cccbdb.nist.gov/inchix.asp":
            return FetchResult(None, 404, f"unallowlisted URL {url}")
        text = (FIXTURES_DIR / "catalog_inchix.html").read_text(
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


class TestCatalogSnapshotHappyPath:
    def test_writes_catalog_prefixed_raw_html(self, tmp_path):
        run_snapshot(CATALOG_PILOT, _make_config(tmp_path, _CatalogFetcher.make()))
        raw_files = sorted((tmp_path / "raw_html").iterdir())
        assert len(raw_files) == 1
        # Catalog archives use the ``catalog_<key>_<sha>.html`` prefix
        # so they coexist with experimental_* and property_* without
        # collision.
        assert raw_files[0].name.startswith("catalog_inchix_")

    def test_writes_parsed_json(self, tmp_path):
        run_snapshot(CATALOG_PILOT, _make_config(tmp_path, _CatalogFetcher.make()))
        parsed_files = sorted((tmp_path / "parsed").iterdir())
        assert len(parsed_files) == 1
        data = json.loads(parsed_files[0].read_text())
        # Top-level shape matches CCCBDBMoleculeCatalog.
        assert (
            data["source_metadata"]["page_kind"]
            == "molecule_catalog_inchi_index"
        )
        assert len(data["entries"]) > 0

    def test_manifest_has_catalog_page_kind(self, tmp_path):
        run_snapshot(CATALOG_PILOT, _make_config(tmp_path, _CatalogFetcher.make()))
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert len(manifest["records"]) == 1
        rec = manifest["records"][0]
        assert rec["page_kind"] == "molecule_catalog_inchi_index"
        assert rec["parser_error"] is None
        assert rec["payload_json_path"] is None

    def test_no_payload_written_for_catalog(self, tmp_path):
        run_snapshot(
            CATALOG_PILOT,
            _make_config(tmp_path, _CatalogFetcher.make(), write_payloads=True),
        )
        assert not (tmp_path / "payloads").exists() or not list(
            (tmp_path / "payloads").iterdir()
        )
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        # The runner records a builder warning so a future maintainer
        # knows the omission was intentional.
        assert any(
            "identity-universe-only" in w
            for w in manifest["records"][0]["builder_warnings"]
        )

    def test_runner_never_fetches_downstream_links(self, tmp_path):
        """The catalog snapshot must hit exactly one URL — the index
        itself — even though the fixture contains rows with hrefs."""

        fetcher = _CatalogFetcher.make()
        run_snapshot(CATALOG_PILOT, _make_config(tmp_path, fetcher))
        assert list(fetcher.call_counts.keys()) == [
            "https://cccbdb.nist.gov/inchix.asp"
        ]


class TestCatalogCacheIsolation:
    """Catalog cache must not collide with the experimental_* or
    property_* cache families that share the same ``raw_html/``
    directory."""

    def test_catalog_does_not_serve_as_experimental_cache(self, tmp_path):
        # Warm the catalog cache.
        run_snapshot(CATALOG_PILOT, _make_config(tmp_path, _CatalogFetcher.make()))

        # Ask for an experimental_species target with species_key="inchix"
        # (same key, different page_kind). Cache lookup must miss.
        species_target = CrawlTarget(
            species_key="inchix",
            source_url="https://cccbdb.nist.gov/exp1x.asp?placeholder",
            page_kind="experimental_species",
        )

        def exploding(url):
            raise AssertionError("must not fetch — testing cache miss path")

        manifest = run_snapshot(
            (species_target,),
            _make_config(tmp_path, exploding, dry_run=True),
        )
        record = manifest["records"][0]
        assert record["cache_hit"] is False

    def test_catalog_does_not_serve_as_property_cache(self, tmp_path):
        run_snapshot(CATALOG_PILOT, _make_config(tmp_path, _CatalogFetcher.make()))

        property_target = CrawlTarget(
            species_key="inchix",
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
        record = manifest["records"][0]
        assert record["cache_hit"] is False


class TestCatalogPilotRegistration:
    def test_catalog_pilot_exposes_single_target(self):
        assert len(CATALOG_PILOT) == 1
        target = CATALOG_PILOT[0]
        assert target.page_kind == "molecule_catalog_inchi_index"
        assert target.is_validated_url is True
        # No property_kind on catalog targets.
        assert target.property_kind is None
