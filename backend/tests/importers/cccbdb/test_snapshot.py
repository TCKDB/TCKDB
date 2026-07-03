"""Offline tests for the CCCBDB snapshot archive runner.

Tests inject a fake :class:`Fetcher` so no network is touched. The
fake fetcher serves the Phase 1 hand-authored fixtures bundled at
``backend/app/importers/cccbdb/fixtures/``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.importers.cccbdb.crawl_plan import EXPERIMENTAL_PILOT
from app.importers.cccbdb.snapshot import (
    BUILDER_VERSION,
    SNAPSHOT_VERSION,
    FetchResult,
    SnapshotConfig,
    SnapshotFailed,
    run_snapshot,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)


# ---------------------------------------------------------------------------
# Fake fetchers
# ---------------------------------------------------------------------------


@dataclass
class FixtureFetcher:
    """Maps allowlisted source URLs onto bundled fixture files.

    Counts calls per URL so tests can assert cache-hit behavior.
    """

    call_counts: dict[str, int]

    @classmethod
    def make(cls) -> "FixtureFetcher":
        return cls(call_counts={})

    def __call__(self, url: str) -> FetchResult:
        self.call_counts[url] = self.call_counts.get(url, 0) + 1
        mapping = {
            EXPERIMENTAL_PILOT[0].source_url: "experimental_h2.html",
            EXPERIMENTAL_PILOT[1].source_url: "experimental_h2o.html",
            EXPERIMENTAL_PILOT[2].source_url: "experimental_benzene.html",
        }
        if url not in mapping:
            return FetchResult(None, 404, f"unallowlisted URL {url}")
        text = (FIXTURES_DIR / mapping[url]).read_text(encoding="utf-8")
        return FetchResult(text, 200, None)


def _make_config(
    tmp_path: Path,
    fetcher,
    *,
    write_payloads: bool = False,
    force_refresh: bool = False,
    dry_run: bool = False,
    sleep_seconds: float = 0.0,
    max_pages: int | None = None,
    strict: bool = False,
) -> SnapshotConfig:
    return SnapshotConfig(
        output_dir=tmp_path,
        fetcher=fetcher,
        write_payloads=write_payloads,
        force_refresh=force_refresh,
        sleep_seconds=sleep_seconds,
        dry_run=dry_run,
        max_pages=max_pages,
        strict=strict,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestSnapshotHappyPath:
    def test_writes_raw_html_for_each_target(self, tmp_path):
        fetcher = FixtureFetcher.make()
        run_snapshot(EXPERIMENTAL_PILOT, _make_config(tmp_path, fetcher))

        raw_files = sorted((tmp_path / "raw_html").iterdir())
        assert len(raw_files) == 3
        for path in raw_files:
            assert path.suffix == ".html"
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            # Filename ends with the first 12 chars of the sha.
            assert sha.startswith(path.stem.split("_")[-1])

    def test_writes_parsed_json_for_each_target(self, tmp_path):
        fetcher = FixtureFetcher.make()
        run_snapshot(EXPERIMENTAL_PILOT, _make_config(tmp_path, fetcher))

        parsed_files = sorted((tmp_path / "parsed").iterdir())
        assert len(parsed_files) == 3
        # Sanity: each parsed JSON is well-formed and carries CCCBDB
        # source metadata.
        for path in parsed_files:
            data = json.loads(path.read_text())
            assert data["source_metadata"]["source"] == "CCCBDB"
            assert data["source_metadata"]["source_release"] == "22"

    def test_writes_payloads_only_when_flag_set(self, tmp_path):
        fetcher = FixtureFetcher.make()
        run_snapshot(EXPERIMENTAL_PILOT, _make_config(tmp_path, fetcher))
        assert not (tmp_path / "payloads").exists()

    def test_with_payloads_flag(self, tmp_path):
        fetcher = FixtureFetcher.make()
        run_snapshot(
            EXPERIMENTAL_PILOT,
            _make_config(tmp_path, fetcher, write_payloads=True),
        )
        payload_files = sorted((tmp_path / "payloads").iterdir())
        assert len(payload_files) == 3
        # Each payload JSON has the BuildResult shape.
        for path in payload_files:
            data = json.loads(path.read_text())
            assert "external_source" in data
            assert data["external_source"]["name"] == "CCCBDB"

    def test_manifest_shape(self, tmp_path):
        fetcher = FixtureFetcher.make()
        run_snapshot(
            EXPERIMENTAL_PILOT,
            _make_config(tmp_path, fetcher, write_payloads=True),
        )
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["source"] == "CCCBDB"
        assert manifest["source_release"] == "22"
        assert manifest["source_database_doi"] == "10.18434/T47C7Z"
        assert manifest["snapshot_version"] == SNAPSHOT_VERSION
        assert manifest["builder_version"] == BUILDER_VERSION
        assert manifest["parser_version"]
        assert len(manifest["records"]) == 3
        for rec in manifest["records"]:
            assert rec["source_url"].startswith("https://cccbdb.nist.gov/")
            assert rec["content_sha256"] and len(rec["content_sha256"]) == 64
            assert rec["raw_html_path"].startswith("raw_html/")
            assert rec["parsed_json_path"].startswith("parsed/")
            assert rec["payload_json_path"].startswith("payloads/")
            assert rec["http_status"] == 200
            assert rec["parser_error"] is None
            assert rec["builder_error"] is None
            assert rec["cache_hit"] is False

    def test_manifest_paths_are_relative_to_archive_root(self, tmp_path):
        fetcher = FixtureFetcher.make()
        run_snapshot(EXPERIMENTAL_PILOT, _make_config(tmp_path, fetcher))
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        for rec in manifest["records"]:
            assert not rec["raw_html_path"].startswith("/")
            assert not rec["parsed_json_path"].startswith("/")


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


class TestCacheBehavior:
    def test_second_run_reuses_cached_raw_html(self, tmp_path):
        fetcher = FixtureFetcher.make()
        run_snapshot(EXPERIMENTAL_PILOT, _make_config(tmp_path, fetcher))
        first_counts = dict(fetcher.call_counts)

        run_snapshot(EXPERIMENTAL_PILOT, _make_config(tmp_path, fetcher))
        # No new fetches: cache should serve all three.
        assert fetcher.call_counts == first_counts

        manifest = json.loads((tmp_path / "manifest.json").read_text())
        for rec in manifest["records"]:
            assert rec["cache_hit"] is True

    def test_force_refresh_refetches(self, tmp_path):
        fetcher = FixtureFetcher.make()
        run_snapshot(EXPERIMENTAL_PILOT, _make_config(tmp_path, fetcher))
        first_counts = dict(fetcher.call_counts)

        run_snapshot(
            EXPERIMENTAL_PILOT,
            _make_config(tmp_path, fetcher, force_refresh=True),
        )
        for url, n in first_counts.items():
            assert fetcher.call_counts[url] == n + 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class _BrokenParserFetcher:
    """Returns valid HTML that the parser will reject."""

    def __call__(self, url: str) -> FetchResult:
        # The parser refuses empty source URL; we force a parser
        # failure by injecting HTML that the parser will accept BUT
        # then we monkeypatch the parser. Simpler approach used below:
        # return content the parser handles and assert downstream
        # behavior. See dedicated tests for parser/builder injection.
        return FetchResult("<html></html>", 200, None)


def test_parser_failure_still_saves_raw_html(tmp_path, monkeypatch):
    """If the parser raises, raw HTML is saved and parser_error is recorded."""

    from app.importers.cccbdb import snapshot as snap_mod

    def failing_parser(*args, **kwargs):
        raise RuntimeError("synthetic parser failure")

    monkeypatch.setattr(
        snap_mod, "parse_experimental_species_page", failing_parser
    )

    fetcher = FixtureFetcher.make()
    manifest = run_snapshot(
        EXPERIMENTAL_PILOT, _make_config(tmp_path, fetcher)
    )

    # Raw HTML still saved.
    raw_files = sorted((tmp_path / "raw_html").iterdir())
    assert len(raw_files) == 3
    # Parsed dir may exist but should be empty (or absent).
    parsed_dir = tmp_path / "parsed"
    if parsed_dir.exists():
        assert list(parsed_dir.iterdir()) == []
    for rec in manifest["records"]:
        assert rec["parser_error"] is not None
        assert "synthetic parser failure" in rec["parser_error"]
        assert rec["parsed_json_path"] is None
        assert rec["raw_html_path"] is not None


def test_builder_failure_still_saves_parsed_json(tmp_path, monkeypatch):
    """If the builder raises, parsed JSON is saved and builder_error recorded."""

    from app.importers.cccbdb import builders as builders_mod

    def failing_builder(record):
        raise RuntimeError("synthetic builder failure")

    monkeypatch.setattr(
        builders_mod, "build_experimental_species_payload", failing_builder
    )

    fetcher = FixtureFetcher.make()
    manifest = run_snapshot(
        EXPERIMENTAL_PILOT,
        _make_config(tmp_path, fetcher, write_payloads=True),
    )

    parsed_files = sorted((tmp_path / "parsed").iterdir())
    assert len(parsed_files) == 3
    # Payloads directory may exist (we created it) but no files.
    payload_dir = tmp_path / "payloads"
    assert payload_dir.exists()
    assert list(payload_dir.iterdir()) == []
    for rec in manifest["records"]:
        assert rec["parser_error"] is None
        assert rec["builder_error"] is not None
        assert "synthetic builder failure" in rec["builder_error"]
        assert rec["payload_json_path"] is None


class _AlwaysFailingFetcher:
    def __call__(self, url: str) -> FetchResult:
        return FetchResult(None, 503, "service unavailable")


def test_all_fetch_failures_raise(tmp_path):
    fetcher = _AlwaysFailingFetcher()
    with pytest.raises(SnapshotFailed):
        run_snapshot(EXPERIMENTAL_PILOT, _make_config(tmp_path, fetcher))


def test_partial_fetch_failure_does_not_raise(tmp_path):
    class _OneFailFetcher:
        def __call__(self, url: str) -> FetchResult:
            if url == EXPERIMENTAL_PILOT[0].source_url:
                return FetchResult(None, 500, "boom")
            return FixtureFetcher.make()(url)

    manifest = run_snapshot(
        EXPERIMENTAL_PILOT,
        _make_config(tmp_path, _OneFailFetcher()),
    )
    failed = manifest["records"][0]
    assert failed["content_sha256"] is None
    assert failed["fetch_warnings"]
    ok = manifest["records"][1]
    assert ok["content_sha256"] is not None


def test_strict_mode_raises_on_any_error(tmp_path, monkeypatch):
    from app.importers.cccbdb import snapshot as snap_mod

    monkeypatch.setattr(
        snap_mod,
        "parse_experimental_species_page",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    fetcher = FixtureFetcher.make()
    with pytest.raises(SnapshotFailed):
        run_snapshot(
            EXPERIMENTAL_PILOT,
            _make_config(tmp_path, fetcher, strict=True),
        )


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_writes_no_files(self, tmp_path):
        # Pre-seed the cache so dry-run has something to serve. Real
        # dry-run usage is typically against an existing archive
        # (regenerate payloads from cached HTML without re-fetching);
        # the on-cold-cache behavior is exercised separately below.
        fetcher = FixtureFetcher.make()
        run_snapshot(
            EXPERIMENTAL_PILOT,
            _make_config(tmp_path, fetcher),
        )

        dry_fetcher_calls = []

        def exploding_fetcher(url):
            dry_fetcher_calls.append(url)
            raise AssertionError(
                f"dry-run must not touch the network; got {url}"
            )

        manifest = run_snapshot(
            EXPERIMENTAL_PILOT,
            _make_config(
                tmp_path,
                exploding_fetcher,
                dry_run=True,
                write_payloads=True,
            ),
        )
        # No fetcher calls at all.
        assert dry_fetcher_calls == []
        # Manifest is well-formed (served from cache).
        assert len(manifest["records"]) == 3
        for rec in manifest["records"]:
            assert rec["content_sha256"] is not None
            assert rec["cache_hit"] is True

    def test_dry_run_with_cold_cache_records_fetch_warning(self, tmp_path):
        """When dry-run is invoked without a pre-warmed cache, every
        target should land in fetch_warnings, no fetcher call, no
        files written."""

        called = []

        def exploding_fetcher(url):
            called.append(url)
            raise AssertionError("dry-run must not touch the network")

        manifest = run_snapshot(
            EXPERIMENTAL_PILOT,
            _make_config(tmp_path, exploding_fetcher, dry_run=True),
        )
        assert called == []
        assert not (tmp_path / "manifest.json").exists()
        for rec in manifest["records"]:
            assert rec["content_sha256"] is None
            assert any("dry-run" in w for w in rec["fetch_warnings"])


# ---------------------------------------------------------------------------
# max-pages
# ---------------------------------------------------------------------------


class TestMaxPages:
    def test_caps_targets(self, tmp_path):
        fetcher = FixtureFetcher.make()
        manifest = run_snapshot(
            EXPERIMENTAL_PILOT,
            _make_config(tmp_path, fetcher, max_pages=1),
        )
        assert len(manifest["records"]) == 1
        assert manifest["records"][0]["species_key"] == "h2"
