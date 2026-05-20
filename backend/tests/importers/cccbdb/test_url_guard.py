"""Tests for the unverified-URL guardrail.

The current Phase 2b allowlist contains placeholder ``exp1x.asp``
URLs that CCCBDB does not actually serve as per-species GET endpoints
(see ``crawl_plan.py`` module docstring). The CLI must refuse to fetch
them unless an explicit override flag is passed.
"""

from __future__ import annotations

import pytest

from app.importers.cccbdb.crawl_plan import (
    EXPERIMENTAL_PILOT,
    CrawlTarget,
    UnverifiedUrlError,
    assert_all_validated,
)
from app.importers.cccbdb.snapshot import main


def test_pilot_targets_are_currently_unverified():
    """Sanity: the pilot ships with unverified URLs by design. When
    someone later wires up real per-species URLs, they will flip
    ``is_validated_url=True`` and this test should be updated."""

    assert all(not t.is_validated_url for t in EXPERIMENTAL_PILOT)


def test_assert_all_validated_rejects_unverified():
    with pytest.raises(UnverifiedUrlError) as exc_info:
        assert_all_validated(EXPERIMENTAL_PILOT)
    msg = str(exc_info.value)
    # Error should list each species and its URL so a maintainer can
    # diagnose without re-running.
    assert "h2" in msg
    assert "h2o" in msg
    assert "benzene" in msg
    assert "Cloudflare" in msg


def test_assert_all_validated_accepts_verified():
    verified = (
        CrawlTarget(
            species_key="example",
            source_url="https://example.invalid/data",
            is_validated_url=True,
        ),
    )
    assert_all_validated(verified)  # no exception


def test_cli_refuses_unverified_urls_without_override(tmp_path, caplog):
    """``main()`` should exit with status 2 and a clear log line when
    asked to live-fetch unverified URLs."""

    import logging

    with caplog.at_level(logging.ERROR):
        exit_code = main(
            [
                "--output-dir",
                str(tmp_path),
                "--sleep-seconds",
                "0",
                "--max-pages",
                "1",
            ]
        )
    assert exit_code == 2
    assert "Refusing to fetch unverified" in caplog.text
    # No archive directory created either.
    assert not (tmp_path / "manifest.json").exists()


def test_cli_dry_run_bypasses_url_guard(tmp_path):
    """Dry-run is fully offline (no network), so the URL guard does
    not need to block it. The runner will simply record a fetch
    warning for the cold cache."""

    exit_code = main(
        [
            "--output-dir",
            str(tmp_path),
            "--sleep-seconds",
            "0",
            "--dry-run",
        ]
    )
    assert exit_code == 0
