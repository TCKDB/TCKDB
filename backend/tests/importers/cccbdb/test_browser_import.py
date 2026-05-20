"""Offline tests for the browser-assisted CCCBDB species import.

The browser-import path NEVER touches the network. There is no
``Fetcher`` parameter to inject a fake into — the absence of network
I/O is structural, not behavioral. These tests pin the rest of the
contract: classifier gate, archive layout, manifest merge semantics,
and metadata preservation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.importers.cccbdb.browser_import import (
    BrowserImportConfig,
    import_saved_species_page,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)

_H2O_FIXTURE = FIXTURES_DIR / "species_alldata_h2o.html"
_FORMULA_ENTRY_FIXTURE = FIXTURES_DIR / "species_alldata_formula_entry_live.html"
_REDIRECT_FIXTURE = FIXTURES_DIR / "species_alldata_redirect_landing.html"


# Synthetic fixtures for rate-limit / unknown — written per test
# rather than persisted to fixtures/ because they're never used at
# runtime, only here.
_RATE_LIMIT_HTML = (
    "<html><body><h1>Error 1015</h1>"
    "<p>You are being rate limited by Cloudflare.</p></body></html>"
)
_UNKNOWN_HTML = (
    "<html><body><p>No diagnostic markers anywhere.</p></body></html>"
)


def _config(
    tmp_path: Path,
    *,
    species_key: str = "h2o",
    source_url: str = "https://cccbdb.nist.gov/alldata2x.asp?casno=7732185",
    cas_number: str | None = "7732185",
    input_html_path: Path | None = None,
    **overrides,
) -> BrowserImportConfig:
    return BrowserImportConfig(
        input_html_path=input_html_path or _H2O_FIXTURE,
        output_dir=tmp_path,
        species_key=species_key,
        source_url=source_url,
        cas_number=cas_number,
        **overrides,
    )


# ---------------------------------------------------------------------------
# Accepted-path
# ---------------------------------------------------------------------------


class TestAcceptedMoleculeDataPage:
    def test_raw_html_and_parsed_json_written(self, tmp_path):
        result = import_saved_species_page(_config(tmp_path))
        assert result.record.accepted_as_data is True
        assert result.record.classification == "molecule_data_page"
        # Archive layout matches the regular snapshot's species_alldata_ prefix.
        raw = list((tmp_path / "raw_html").iterdir())
        parsed = list((tmp_path / "parsed").iterdir())
        assert len(raw) == 1
        assert len(parsed) == 1
        assert raw[0].name.startswith("species_alldata_h2o_")
        assert parsed[0].name.startswith("species_alldata_h2o_")

    def test_parsed_json_contains_detected_identifiers(self, tmp_path):
        result = import_saved_species_page(_config(tmp_path))
        parsed_path = tmp_path / result.record.parsed_json_path
        data = json.loads(parsed_path.read_text())
        assert data["detected_inchikey"] == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        assert data["source_metadata"]["page_kind"] == "species_all_data"
        assert data["source_metadata"]["cas_number"] == "7732185"

    def test_no_rejected_html_when_accepted(self, tmp_path):
        result = import_saved_species_page(_config(tmp_path))
        assert result.record.rejected_html_path is None
        # rejected_html/ dir should not even exist on the accepted path.
        assert not (tmp_path / "rejected_html").exists()


# ---------------------------------------------------------------------------
# Rejected-path (all four classifier reject classes)
# ---------------------------------------------------------------------------


class TestFormulaEntryRejected:
    """The Phase 5b classifier hardening must keep firing — a saved
    formula-entry page (the deceptive ``All data for one molecule``
    title with the ``getformx.asp`` form) is never accepted."""

    def test_classification_is_formula_entry(self, tmp_path):
        result = import_saved_species_page(
            _config(
                tmp_path,
                input_html_path=_FORMULA_ENTRY_FIXTURE,
                species_key="broken",
            )
        )
        assert result.record.accepted_as_data is False
        # No final_url is supplied → form text classifies as
        # ``formula_entry_page`` (not redirect_landing).
        assert result.record.classification == "formula_entry_page"

    def test_rejected_page_not_copied_to_raw_html(self, tmp_path):
        import_saved_species_page(
            _config(
                tmp_path,
                input_html_path=_FORMULA_ENTRY_FIXTURE,
                species_key="broken",
            )
        )
        # raw_html/ either doesn't exist or is empty.
        raw_dir = tmp_path / "raw_html"
        if raw_dir.exists():
            assert list(raw_dir.iterdir()) == []
        # And no parsed JSON either.
        parsed_dir = tmp_path / "parsed"
        if parsed_dir.exists():
            assert list(parsed_dir.iterdir()) == []

    def test_save_rejected_html_writes_rejected_dir(self, tmp_path):
        result = import_saved_species_page(
            _config(
                tmp_path,
                input_html_path=_FORMULA_ENTRY_FIXTURE,
                species_key="broken",
                save_rejected_html=True,
            )
        )
        rejected = list((tmp_path / "rejected_html").iterdir())
        assert len(rejected) == 1
        assert rejected[0].name.startswith("species_alldata_broken_")
        assert result.record.rejected_html_path
        assert result.record.rejected_html_path.startswith("rejected_html/")


class TestRedirectLandingRejected:
    """Even when we don't know what the browser actually saved (the
    user just exported a page), pages that LOOK like the form-entry
    HTML are still rejected by the formula-entry signal — the
    classifier doesn't need a redirected URL to flag the form.
    """

    def test_redirect_landing_html_is_rejected(self, tmp_path):
        result = import_saved_species_page(
            _config(
                tmp_path,
                input_html_path=_REDIRECT_FIXTURE,
                species_key="bad",
            )
        )
        # No final_url -> classified as formula_entry_page (the form
        # signal is the same; the redirect-vs-form distinction is a
        # diagnostic-time concern only).
        assert result.record.accepted_as_data is False
        assert result.record.classification in {
            "formula_entry_page",
            "redirect_landing_page",
        }
        # raw_html stays empty.
        if (tmp_path / "raw_html").exists():
            assert list((tmp_path / "raw_html").iterdir()) == []


class TestRateLimitRejected:
    def test_cloudflare_1015_html_rejected(self, tmp_path):
        rate_limit_path = tmp_path / "ratelim.html"
        rate_limit_path.write_text(_RATE_LIMIT_HTML)
        result = import_saved_species_page(
            _config(
                tmp_path,
                input_html_path=rate_limit_path,
                species_key="ratelim",
            )
        )
        assert result.record.accepted_as_data is False
        assert result.record.classification == "rate_limit_or_error_page"


class TestUnknownRejectedByDefault:
    def test_unknown_html_rejected(self, tmp_path):
        unknown_path = tmp_path / "unknown.html"
        unknown_path.write_text(_UNKNOWN_HTML)
        result = import_saved_species_page(
            _config(
                tmp_path,
                input_html_path=unknown_path,
                species_key="mystery",
            )
        )
        assert result.record.accepted_as_data is False
        assert result.record.classification == "unknown"

    def test_unknown_accepted_with_allow_unknown(self, tmp_path):
        """Escape hatch: a maintainer can opt in to accepting
        unknown-classification pages when they trust the source."""

        unknown_path = tmp_path / "unknown.html"
        unknown_path.write_text(_UNKNOWN_HTML)
        result = import_saved_species_page(
            _config(
                tmp_path,
                input_html_path=unknown_path,
                species_key="mystery",
                allow_unknown=True,
            )
        )
        assert result.record.accepted_as_data is True
        assert result.record.classification == "unknown"
        # And raw_html does get written in that case.
        assert list((tmp_path / "raw_html").iterdir())


# ---------------------------------------------------------------------------
# Manifest semantics
# ---------------------------------------------------------------------------


class TestManifestMerge:
    def test_manifest_written_on_first_import(self, tmp_path):
        result = import_saved_species_page(_config(tmp_path))
        manifest = json.loads(result.manifest_path.read_text())
        assert manifest["source"] == "CCCBDB"
        assert manifest["snapshot_version"]
        assert len(manifest["records"]) == 1
        # The new last_modified_at field tracks subsequent merges.
        assert "last_modified_at" in manifest

    def test_second_import_with_same_sha_is_idempotent(self, tmp_path):
        import_saved_species_page(_config(tmp_path))
        result_2 = import_saved_species_page(_config(tmp_path))
        # Same content SHA → same dedupe key → one record total.
        assert result_2.manifest_record_count == 1

    def test_second_import_with_different_sha_appends(self, tmp_path):
        # First import: H2O fixture.
        import_saved_species_page(_config(tmp_path))
        # Second import: same species_key but DIFFERENT content (use
        # a slightly mutated copy of the fixture so the SHA changes).
        mutated_path = tmp_path / "h2o_mutated.html"
        original = _H2O_FIXTURE.read_text()
        mutated_path.write_text(original + "<!-- different content -->")
        result_2 = import_saved_species_page(
            _config(tmp_path, input_html_path=mutated_path)
        )
        assert result_2.manifest_record_count == 2

    def test_manifest_record_includes_resolver_metadata(self, tmp_path):
        result = import_saved_species_page(
            _config(tmp_path, note="Saved via Firefox manual lookup")
        )
        manifest = json.loads(result.manifest_path.read_text())
        rec = manifest["records"][0]
        assert rec["resolver_strategy"] == "browser_saved_html"
        assert rec["cas_number"] == "7732185"
        assert rec["note"] == "Saved via Firefox manual lookup"
        assert rec["page_kind"] == "species_all_data"
        assert rec["source_url"].startswith("https://cccbdb.nist.gov/")

    def test_manifest_interleaves_with_existing_snapshot_records(self, tmp_path):
        """A maintainer may run the regular snapshot runner first,
        then drop in a browser-saved page. The merge must preserve
        the pre-existing records."""

        # Simulate a prior snapshot manifest (matching the regular runner shape).
        prior_manifest = {
            "source": "CCCBDB",
            "source_release": "22",
            "source_database_doi": "10.18434/T47C7Z",
            "snapshot_version": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "parser_version": "test",
            "builder_version": "test",
            "records": [
                {
                    "species_key": "h2",
                    "page_kind": "species_all_data",
                    "content_sha256": "0" * 64,
                    "classification": "molecule_data_page",
                    "accepted_as_data": True,
                }
            ],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(prior_manifest, indent=2))

        import_saved_species_page(_config(tmp_path))
        manifest = json.loads(manifest_path.read_text())
        # Original record preserved + new browser-import record appended.
        assert len(manifest["records"]) == 2
        species_keys = {r["species_key"] for r in manifest["records"]}
        assert species_keys == {"h2", "h2o"}


# ---------------------------------------------------------------------------
# Metadata preservation
# ---------------------------------------------------------------------------


class TestMetadataPreserved:
    def test_source_url_and_cas_in_manifest_and_parsed_json(self, tmp_path):
        result = import_saved_species_page(_config(tmp_path))
        manifest_rec = json.loads(result.manifest_path.read_text())[
            "records"
        ][0]
        assert (
            manifest_rec["source_url"]
            == "https://cccbdb.nist.gov/alldata2x.asp?casno=7732185"
        )
        assert manifest_rec["cas_number"] == "7732185"

        parsed = json.loads(
            (tmp_path / result.record.parsed_json_path).read_text()
        )
        assert (
            parsed["source_metadata"]["source_url"]
            == "https://cccbdb.nist.gov/alldata2x.asp?casno=7732185"
        )
        assert parsed["source_metadata"]["cas_number"] == "7732185"

    def test_content_sha256_deterministic(self, tmp_path):
        a = import_saved_species_page(_config(tmp_path))
        # Re-import (idempotent) — same SHA.
        b = import_saved_species_page(_config(tmp_path))
        assert a.record.content_sha256 == b.record.content_sha256
        assert len(a.record.content_sha256) == 64


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


class TestArgValidation:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            import_saved_species_page(
                _config(tmp_path, input_html_path=tmp_path / "nope.html")
            )

    def test_empty_species_key_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            import_saved_species_page(
                _config(tmp_path, species_key=" ")
            )

    def test_empty_source_url_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            import_saved_species_page(_config(tmp_path, source_url=" "))


# ---------------------------------------------------------------------------
# No-network invariant
# ---------------------------------------------------------------------------


def test_browser_import_module_imports_no_requests():
    """Structural guarantee: the browser-import module does not
    import ``requests``. If a maintainer adds a network dependency,
    this test fails loudly."""

    from app.importers.cccbdb import browser_import

    # ``requests`` is imported lazily by ``HttpFetcher`` in snapshot.py,
    # but browser_import.py imports from snapshot at the *module* level.
    # We check the browser_import module's own globals, not its
    # transitive imports.
    assert "requests" not in browser_import.__dict__
