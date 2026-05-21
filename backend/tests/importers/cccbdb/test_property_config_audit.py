"""Tests for the PROPERTY_CONFIGS ↔ Experimental index audit."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers.cccbdb.parsers.experimental_index import (
    parse_experimental_index_page,
)
from app.importers.cccbdb.parsers.experimental_property_table import (
    PROPERTY_CONFIGS,
)
from app.importers.cccbdb.property_config_audit import (
    PropertyConfigAuditResult,
    audit_property_configs,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)


@pytest.fixture(scope="module")
def audit() -> PropertyConfigAuditResult:
    html = (FIXTURES_DIR / "experimental_index_exp2x.html").read_text(
        encoding="utf-8"
    )
    index = parse_experimental_index_page(
        html, source_url="https://cccbdb.nist.gov/exp2x.asp"
    )
    return audit_property_configs(index)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_audit_lists_every_configured_target(audit: PropertyConfigAuditResult):
    configured_kinds = {t.property_kind for t in audit.configured_targets}
    assert configured_kinds == set(PROPERTY_CONFIGS.keys())


def test_audit_matches_known_configured_targets(
    audit: PropertyConfigAuditResult,
):
    """Every configured property_kind must match a live link on the
    Experimental index. If this regresses, the configured source_url
    has gone stale and the importer is about to refetch a 404 page."""

    for kind in PROPERTY_CONFIGS:
        assert kind in audit.matched_targets, (
            f"configured property_kind {kind!r} is no longer advertised "
            f"on the Experimental index — its URL may be stale"
        )


def test_no_unmatched_configured_targets(audit: PropertyConfigAuditResult):
    assert audit.unmatched_configured_targets == []


def test_unconfigured_links_surface_at_least_one_high_value_page(
    audit: PropertyConfigAuditResult,
):
    """The audit should surface obvious extension targets (quadrupole
    list, atomization energy, …) that the pilot has not configured
    yet. Tests one specific landmark so the audit is not silently
    swallowing the unconfigured list."""

    hrefs = {link.href for link in audit.unconfigured_experimental_links}
    assert "quadlistx.asp" in hrefs


# ---------------------------------------------------------------------------
# JSON shape
# ---------------------------------------------------------------------------


def test_audit_to_json_keys(audit: PropertyConfigAuditResult):
    payload = audit.to_json()
    expected_keys = {
        "configured_targets",
        "experimental_index_links",
        "matched_targets",
        "unmatched_configured_targets",
        "unconfigured_experimental_links",
    }
    assert expected_keys <= set(payload.keys())
