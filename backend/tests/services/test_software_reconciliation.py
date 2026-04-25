"""Tests for software provenance reconciliation.

Covers:
  1. All five match statuses (matched, enriched, parsed_only, declared_only, mismatch)
  2. Field-level comparison: name, version, revision
  3. Edge cases: missing fields, wrong software entirely
  4. Integration with real Gaussian log files
"""

from __future__ import annotations

import os

import pytest

from app.schemas.fragments.refs import SoftwareReleaseRef
from app.services.gaussian_parameter_parser import parse_software_version
from app.services.software_reconciliation import (
    SoftwareReconciliationResult,
    parsed_dict_to_ref,
    reconcile_software_provenance,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
GAUSSIAN_DIR = os.path.join(FIXTURES_DIR, "gaussian")
OPT_LOG = os.path.join(GAUSSIAN_DIR, "opt_g09.log")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parsed_gaussian09() -> dict:
    """Parser output for Gaussian 09 RevD.01."""
    return {
        "name": "gaussian",
        "version": "09",
        "build": "EM64L-G09RevD.01",
        "release_date_raw": "24-Apr-2013",
    }


# ---------------------------------------------------------------------------
# 1. parsed_dict_to_ref conversion
# ---------------------------------------------------------------------------


class TestParsedDictToRef:
    def test_gaussian_build_string_extracts_revision(self, parsed_gaussian09):
        ref = parsed_dict_to_ref(parsed_gaussian09)
        assert ref.name == "gaussian"
        assert ref.version == "09"
        assert ref.revision == "D.01"
        assert ref.build == "EM64L-G09RevD.01"

    def test_no_revision_in_build_returns_none(self):
        parsed = {"name": "orca", "version": "5.0.3", "build": "linux_x86-64"}
        ref = parsed_dict_to_ref(parsed)
        assert ref.revision is None
        assert ref.build == "linux_x86-64"


# ---------------------------------------------------------------------------
# 2. Reconciliation — all five statuses
# ---------------------------------------------------------------------------


class TestReconciliationStatuses:
    def test_matched(self, parsed_gaussian09):
        """User declares correct version and revision → matched."""
        declared = SoftwareReleaseRef(name="gaussian", version="09", revision="D.01")
        result = reconcile_software_provenance(
            declared=declared, parsed=parsed_gaussian09
        )
        assert result.match_status == "matched"
        assert result.mismatches == {}
        assert result.resolved_ref is declared

    def test_enriched_version_and_revision(self, parsed_gaussian09):
        """User gives name only, parser fills version + revision."""
        declared = SoftwareReleaseRef(name="gaussian")
        result = reconcile_software_provenance(
            declared=declared, parsed=parsed_gaussian09
        )
        assert result.match_status == "enriched"
        assert "version" in result.mismatches
        assert "revision" in result.mismatches
        assert result.resolved_ref.version == "09"
        assert result.resolved_ref.revision == "D.01"

    def test_enriched_revision_only(self, parsed_gaussian09):
        """User gives name + version, parser fills revision."""
        declared = SoftwareReleaseRef(name="gaussian", version="09")
        result = reconcile_software_provenance(
            declared=declared, parsed=parsed_gaussian09
        )
        assert result.match_status == "enriched"
        assert "revision" in result.mismatches
        assert result.resolved_ref.revision == "D.01"
        assert result.resolved_ref.version == "09"

    def test_parsed_only(self, parsed_gaussian09):
        """User provides nothing, parser extracts everything."""
        result = reconcile_software_provenance(
            declared=None, parsed=parsed_gaussian09
        )
        assert result.match_status == "parsed_only"
        assert result.resolved_ref.name == "gaussian"
        assert result.resolved_ref.version == "09"

    def test_declared_only(self):
        """User provides info, no parseable log."""
        declared = SoftwareReleaseRef(name="gaussian", version="09", revision="D.01")
        result = reconcile_software_provenance(declared=declared, parsed=None)
        assert result.match_status == "declared_only"
        assert result.resolved_ref is declared


# ---------------------------------------------------------------------------
# 3. Mismatch detection
# ---------------------------------------------------------------------------


class TestMismatchDetection:
    def test_wrong_version(self, parsed_gaussian09):
        """User says G16 but log says G09."""
        declared = SoftwareReleaseRef(name="gaussian", version="16")
        result = reconcile_software_provenance(
            declared=declared, parsed=parsed_gaussian09
        )
        assert result.match_status == "mismatch"
        assert "version" in result.mismatches
        assert result.mismatches["version"] == ("16", "09")
        # On mismatch, declared takes precedence
        assert result.resolved_ref is declared

    def test_wrong_software_entirely(self, parsed_gaussian09):
        """User says ORCA but log says Gaussian."""
        declared = SoftwareReleaseRef(name="orca", version="5.0.3")
        result = reconcile_software_provenance(
            declared=declared, parsed=parsed_gaussian09
        )
        assert result.match_status == "mismatch"
        assert "name" in result.mismatches
        assert "version" in result.mismatches

    def test_wrong_revision(self, parsed_gaussian09):
        """User says RevC.01 but log says RevD.01."""
        declared = SoftwareReleaseRef(
            name="gaussian", version="09", revision="C.01"
        )
        result = reconcile_software_provenance(
            declared=declared, parsed=parsed_gaussian09
        )
        assert result.match_status == "mismatch"
        assert "revision" in result.mismatches
        assert result.mismatches["revision"] == ("C.01", "D.01")


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_both_none_raises(self):
        with pytest.raises(ValueError, match="No software provenance"):
            reconcile_software_provenance(declared=None, parsed=None)

    def test_case_insensitive_name_match(self, parsed_gaussian09):
        """'Gaussian' vs 'gaussian' should not be a mismatch."""
        declared = SoftwareReleaseRef(name="Gaussian", version="09", revision="D.01")
        result = reconcile_software_provenance(
            declared=declared, parsed=parsed_gaussian09
        )
        assert result.match_status == "matched"

    def test_raw_banner_preserved(self, parsed_gaussian09):
        """The raw build string should be preserved in the result."""
        result = reconcile_software_provenance(
            declared=None, parsed=parsed_gaussian09
        )
        assert result.raw_banner == "EM64L-G09RevD.01"


# ---------------------------------------------------------------------------
# 5. Integration with real log files
# ---------------------------------------------------------------------------


class TestRealLogIntegration:
    def test_reconcile_from_opt_log(self):
        """Full pipeline: parse log → reconcile with user declaration."""
        with open(OPT_LOG) as f:
            text = f.read()
        parsed = parse_software_version(text)
        assert parsed is not None

        declared = SoftwareReleaseRef(name="gaussian", version="09", revision="D.01")
        result = reconcile_software_provenance(declared=declared, parsed=parsed)
        assert result.match_status == "matched"

    def test_auto_fill_from_opt_log(self):
        """Parser fills everything when user provides nothing."""
        with open(OPT_LOG) as f:
            text = f.read()
        parsed = parse_software_version(text)
        result = reconcile_software_provenance(declared=None, parsed=parsed)
        assert result.match_status == "parsed_only"
        assert result.resolved_ref.version == "09"
        assert result.resolved_ref.revision == "D.01"
