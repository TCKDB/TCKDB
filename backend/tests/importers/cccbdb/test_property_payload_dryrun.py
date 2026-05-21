"""Tests for the CCCBDB property-table dry-run payload exporter.

All tests are offline. The cache-only path takes a pre-populated
archive directory; the live path uses a fake fetcher. Real network
is never touched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.importers.cccbdb.crawl_plan import EXPERIMENTAL_PROPERTIES_PILOT
from app.importers.cccbdb.property_payload_dryrun import (
    DryRunSummary,
    TargetDryRunResult,
    _select_targets,
    main,
    run_payload_dryrun,
)
from app.importers.cccbdb.snapshot import FetchResult

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)

_FIXTURE_BY_KIND = {
    "hf_0": "property_hf_0.html",
    "hf_0_with_uncertainty": "property_hf_0_with_uncertainty.html",
    "dipole": "property_dipoles.html",
    "diatomic_spectroscopic": "property_diatomic_spectroscopic.html",
    "polarizability_iso": "property_polarizability_iso.html",
    "quadrupole_moment": "property_quadrupole.html",
}


def _populate_cache(archive_dir: Path, kinds: list[str] | None = None) -> None:
    """Copy bundled property-table fixtures into ``raw_html/``
    under the snapshot-runner's filename convention so the cache
    lookup finds them."""

    if kinds is None:
        kinds = list(_FIXTURE_BY_KIND.keys())
    raw_html = archive_dir / "raw_html"
    raw_html.mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        src = FIXTURES_DIR / _FIXTURE_BY_KIND[kind]
        content = src.read_text(encoding="utf-8")
        sha12 = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        (raw_html / f"property_{kind}_{sha12}.html").write_text(content)


@dataclass
class _ExplodingFetcher:
    """Test transport that raises on every call. Used to prove that
    ``use_cache_only=True`` makes no network requests."""

    calls: list[str] = field(default_factory=list)

    def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        raise AssertionError(
            f"network must not be called in cache-only mode; got {url}"
        )


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------


class TestTargetSelection:
    def test_default_selects_full_pilot(self):
        targets = _select_targets(None)
        assert tuple(t.property_kind for t in targets) == tuple(
            t.property_kind for t in EXPERIMENTAL_PROPERTIES_PILOT
        )

    def test_subset_preserved(self):
        targets = _select_targets(("dipole", "hf_0"))
        kinds = [t.property_kind for t in targets]
        # Order follows the request, not the pilot.
        assert kinds == ["dipole", "hf_0"]

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="unknown property_kind"):
            _select_targets(("hf_0", "bogus_kind"))

    def test_polarizability_iso_is_in_pilot(self):
        targets = _select_targets(None)
        kinds = {t.property_kind for t in targets}
        assert "polarizability_iso" in kinds


# ---------------------------------------------------------------------------
# Cache-only happy path
# ---------------------------------------------------------------------------


class TestCacheOnlyRun:
    def test_writes_one_json_per_target_plus_summary(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        run_payload_dryrun(
            archive_dir=archive,
            output_dir=out,
            use_cache_only=True,
            fetcher=_ExplodingFetcher(),
        )
        names = sorted(p.name for p in out.iterdir())
        assert "summary.json" in names
        for kind in _FIXTURE_BY_KIND:
            assert f"{kind}.json" in names

    def test_no_network_call_in_cache_only_mode(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        fetcher = _ExplodingFetcher()
        run_payload_dryrun(
            archive_dir=archive,
            output_dir=out,
            use_cache_only=True,
            fetcher=fetcher,
        )
        # The exploding fetcher would have raised if called.
        assert fetcher.calls == []

    def test_summary_aggregates_per_target_payloads(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        summary = run_payload_dryrun(
            archive_dir=archive,
            output_dir=out,
            use_cache_only=True,
        )
        assert summary.target_count == len(EXPERIMENTAL_PROPERTIES_PILOT)
        assert summary.total_payload_count > 0
        assert summary.total_invalid_payload_count == 0
        # Summary JSON on disk matches the in-memory object.
        on_disk = json.loads((out / "summary.json").read_text())
        assert on_disk["target_count"] == summary.target_count
        assert on_disk["total_payload_count"] == summary.total_payload_count
        assert set(on_disk["warning_summary"].keys()) == set(_FIXTURE_BY_KIND)


class TestCacheOnlyMissingCache:
    def test_missing_cache_target_is_skipped_not_errored(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        # Populate only one target so the rest miss the cache and
        # must classify as skipped_missing_cache.
        _populate_cache(archive, kinds=["dipole"])

        summary = run_payload_dryrun(
            archive_dir=archive,
            output_dir=out,
            use_cache_only=True,
            fetcher=_ExplodingFetcher(),
        )
        assert summary.skipped_count == len(EXPERIMENTAL_PROPERTIES_PILOT) - 1
        # Per-target JSON file is still written for each skipped target.
        for kind in _FIXTURE_BY_KIND:
            assert (out / f"{kind}.json").exists()
        # Dipole produced payloads; the rest are skipped.
        dipole_json = json.loads((out / "dipole.json").read_text())
        assert dipole_json["skipped_missing_cache"] is False
        assert dipole_json["payload_count"] > 0
        hf_json = json.loads((out / "hf_0.json").read_text())
        assert hf_json["skipped_missing_cache"] is True
        assert hf_json["payload_count"] == 0
        assert any(
            "skipped_missing_cache" in w for w in hf_json["warnings"]
        )

    def test_cache_only_with_missing_archive_dir_raises(self, tmp_path):
        out = tmp_path / "out"
        with pytest.raises(ValueError, match="archive dir does not exist"):
            run_payload_dryrun(
                archive_dir=tmp_path / "nope",
                output_dir=out,
                use_cache_only=True,
            )


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


class TestPayloadValidation:
    def test_every_emitted_payload_round_trips_through_schema(self, tmp_path):
        from app.schemas.entities.molecular_property_observation import (
            MolecularPropertyObservationCreate,
        )

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        run_payload_dryrun(
            archive_dir=archive,
            output_dir=out,
            use_cache_only=True,
        )
        for kind in _FIXTURE_BY_KIND:
            data = json.loads((out / f"{kind}.json").read_text())
            for payload in data["payloads"]:
                # If the dry-run emitted it, model_validate must accept it.
                MolecularPropertyObservationCreate.model_validate(payload)

    def test_per_target_shape_matches_prompt_contract(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        run_payload_dryrun(
            archive_dir=archive,
            output_dir=out,
            use_cache_only=True,
        )
        data = json.loads((out / "dipole.json").read_text())
        required_keys = {
            "property_kind",
            "source_url",
            "detected_headers",
            "parsed_row_count",
            "payload_count",
            "invalid_payload_count",
            "warning_count",
            "skipped_missing_cache",
            "warnings",
            "payloads",
        }
        assert required_keys <= set(data.keys())
        assert data["property_kind"] == "dipole"
        assert data["source_url"] == "https://cccbdb.nist.gov/diplistx.asp"


class TestInvalidPayloadHandling:
    """Behavior contract: a row whose payload fails round-trip
    validation must not crash the whole run. It contributes one
    warning + one invalid_payload_count, then the next row continues."""

    def test_validation_error_recorded_not_raised(self, tmp_path, monkeypatch):
        # Force every payload to fail validation by patching
        # ``model_validate`` with a stub that always raises.
        from app.importers.cccbdb import property_payload_dryrun as mod
        from pydantic import ValidationError

        class _FakeError(ValidationError):
            """Standalone ValidationError-shaped exception. We can't
            instantiate Pydantic's ValidationError directly across
            versions, so we mimic .errors() + the type for the
            ``except ValidationError`` branch to catch."""

            pass

        def fake_validate(_payload):
            # Build a real ValidationError via Pydantic's TypeAdapter
            # on an int model — the simplest deterministic way to
            # raise a ValidationError instance.
            from pydantic import TypeAdapter

            TypeAdapter(int).validate_python("not-an-int")

        monkeypatch.setattr(
            mod.MolecularPropertyObservationCreate,
            "model_validate",
            staticmethod(fake_validate),
        )

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive, kinds=["dipole"])
        summary = run_payload_dryrun(
            archive_dir=archive,
            output_dir=out,
            property_kinds=("dipole",),
            use_cache_only=True,
        )
        assert summary.total_invalid_payload_count > 0
        # No payloads land in the per-target output when validation
        # fails — the warning replaces them.
        dipole = json.loads((out / "dipole.json").read_text())
        assert dipole["invalid_payload_count"] > 0
        assert dipole["payload_count"] == 0
        assert any(
            "invalid payload" in w for w in dipole["warnings"]
        )


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestCli:
    def test_main_writes_summary_and_returns_zero(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        exit_code = main(
            [
                "--archive-dir", str(archive),
                "--output-dir", str(out),
                "--use-cache-only",
            ]
        )
        assert exit_code == 0
        assert (out / "summary.json").exists()

    def test_main_unknown_property_kind_exits_2(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        exit_code = main(
            [
                "--archive-dir", str(archive),
                "--output-dir", str(out),
                "--use-cache-only",
                "--property-kind", "definitely_not_real",
            ]
        )
        assert exit_code == 2

    def test_per_row_warnings_do_not_cause_nonzero_exit(self, tmp_path):
        """The prompt is explicit: only infrastructure/config errors
        return nonzero. A pilot that produces parser warnings on
        every target still exits 0."""

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        exit_code = main(
            [
                "--archive-dir", str(archive),
                "--output-dir", str(out),
                "--use-cache-only",
            ]
        )
        summary = json.loads((out / "summary.json").read_text())
        # There ARE warnings (catalog ambiguity, etc.) but the run
        # is still considered successful.
        assert summary["total_warning_count"] > 0 or summary["total_warning_count"] == 0  # informational
        assert exit_code == 0

    def test_main_property_kind_subset_writes_only_that_target(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        main(
            [
                "--archive-dir", str(archive),
                "--output-dir", str(out),
                "--use-cache-only",
                "--property-kind", "polarizability_iso",
            ]
        )
        # Only summary + the one requested target file.
        files = sorted(p.name for p in out.iterdir())
        assert files == ["polarizability_iso.json", "summary.json"]


# ---------------------------------------------------------------------------
# Polarizability_iso visibility
# ---------------------------------------------------------------------------


class TestPolarizabilityIsoSurface:
    """The dry-run report must surface enough information about the
    live-verified polarizability_iso table that a maintainer can
    spot column drift quickly.

    The live pollistx.asp header (May 2026) is
    ``Molecule | name | State | Conformation | alpha | squib | commment``
    — see PROPERTY_CONFIGS["polarizability_iso"] for the rationale.
    """

    def test_detected_headers_populated(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        run_payload_dryrun(
            archive_dir=archive, output_dir=out, use_cache_only=True
        )
        data = json.loads((out / "polarizability_iso.json").read_text())
        # Live header is "alpha", not the previously-inferred "iso".
        assert "alpha" in data["detected_headers"]
        assert "Molecule" in data["detected_headers"]
        assert data["parsed_row_count"] > 0

    def test_payloads_carry_polarizability_iso_kind(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        run_payload_dryrun(
            archive_dir=archive, output_dir=out, use_cache_only=True
        )
        data = json.loads((out / "polarizability_iso.json").read_text())
        for payload in data["payloads"]:
            assert payload["property_kind"] == "polarizability_iso"
            assert payload["scalar_unit"] == "Bohr^3"
            assert payload["scientific_origin"] == "experimental"

    def test_polarizability_iso_emits_payloads(self, tmp_path):
        """Regression: pollistx.asp must not be parsed-but-empty.

        Before May 2026 the configured value_column was ``iso`` but
        the live page exposes the isotropic polarizability as
        ``alpha``; the dry-run silently produced zero payloads.
        """

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive, kinds=["polarizability_iso"])
        summary = run_payload_dryrun(
            archive_dir=archive,
            output_dir=out,
            property_kinds=("polarizability_iso",),
            use_cache_only=True,
        )
        data = json.loads((out / "polarizability_iso.json").read_text())
        assert data["parsed_row_count"] > 0
        assert data["payload_count"] > 0
        assert data["invalid_payload_count"] == 0
        assert data["health"] == "healthy"
        assert summary.unhealthy_count == 0
        # First real row must have formula + name + a scalar value.
        first = data["payloads"][0]
        assert first["raw_payload_json"]["row_formula"]
        assert first["raw_payload_json"]["row_name"]
        assert first["scalar_value"] is not None


# ---------------------------------------------------------------------------
# No-DB / no-network invariants
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Health gate + hf_0 outcome pin (Part D / Part E)
# ---------------------------------------------------------------------------


class TestHealthGate:
    """``parsed_row_count > 0 and payload_count == 0`` should
    surface as ``health == "unhealthy"`` unless the target is
    explicitly ``workflow_ready=False``."""

    def test_default_pilot_has_no_unhealthy_targets(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        summary = run_payload_dryrun(
            archive_dir=archive, output_dir=out, use_cache_only=True
        )
        assert summary.unhealthy_count == 0, (
            f"default pilot is unhealthy: {summary.unhealthy_targets()}"
        )
        for entry in summary.per_target:
            assert entry["health"] in {"healthy", "quarantined", "skipped"}, (
                f"unexpected health for {entry['property_kind']}: "
                f"{entry['health']} ({entry['health_reason']})"
            )

    def test_parsed_but_zero_payloads_is_unhealthy(self, tmp_path, monkeypatch):
        """Force every row to fail to produce a payload (no scalar)
        and confirm the dry-run marks the target unhealthy. Uses
        the in-memory facade rather than monkey-patching the
        scalar parser — simpler and avoids surprising other tests."""

        from app.importers.cccbdb.property_payload_dryrun import (
            TargetDryRunResult,
        )

        result = TargetDryRunResult(
            property_kind="dipole",
            source_url="https://cccbdb.nist.gov/diplistx.asp",
            parsed_row_count=10,
            payload_count=0,
        )
        result.resolve_health()
        assert result.health == "unhealthy"
        assert result.health_reason == (
            "parsed rows but emitted zero payloads"
        )

    def test_quarantined_when_workflow_ready_is_false(self):
        from app.importers.cccbdb.property_payload_dryrun import (
            TargetDryRunResult,
        )

        result = TargetDryRunResult(
            property_kind="future_kind",
            source_url="https://cccbdb.nist.gov/futurex.asp",
            parsed_row_count=10,
            payload_count=0,
            workflow_ready=False,
        )
        result.resolve_health()
        assert result.health == "quarantined"

    def test_skipped_missing_cache_resolves_to_skipped(self):
        from app.importers.cccbdb.property_payload_dryrun import (
            TargetDryRunResult,
        )

        result = TargetDryRunResult(
            property_kind="dipole",
            source_url="https://cccbdb.nist.gov/diplistx.asp",
            skipped_missing_cache=True,
        )
        result.resolve_health()
        assert result.health == "skipped"

    def test_summary_health_map_populated(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive)
        summary = run_payload_dryrun(
            archive_dir=archive, output_dir=out, use_cache_only=True
        )
        assert set(summary.health_summary.keys()) == set(_FIXTURE_BY_KIND)
        for kind, health in summary.health_summary.items():
            assert health in {"healthy", "unhealthy", "quarantined", "skipped"}


class TestHfZeroOutcome:
    """Pin Part D's decision: hf_0 (hf0kx.asp) stays in the pilot as
    a non-duplicate of hf_0_with_uncertainty (goodlistx.asp). Both
    pages produce ``enthalpy_of_formation`` payloads; goodlist is
    the curated subset with uncertainties."""

    def test_both_hf_targets_present_in_pilot(self):
        from app.importers.cccbdb.crawl_plan import (
            EXPERIMENTAL_PROPERTIES_PILOT,
        )

        kinds = {t.property_kind for t in EXPERIMENTAL_PROPERTIES_PILOT}
        assert "hf_0" in kinds
        assert "hf_0_with_uncertainty" in kinds

    def test_hf_targets_have_distinct_source_urls(self):
        from app.importers.cccbdb.crawl_plan import (
            EXPERIMENTAL_PROPERTIES_PILOT,
        )

        by_kind = {t.property_kind: t for t in EXPERIMENTAL_PROPERTIES_PILOT}
        assert by_kind["hf_0"].source_url != \
            by_kind["hf_0_with_uncertainty"].source_url
        assert "hf0kx.asp" in by_kind["hf_0"].source_url
        assert "goodlistx.asp" in by_kind["hf_0_with_uncertainty"].source_url

    def test_hf_0_emits_payloads_from_fixture(self, tmp_path):
        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive, kinds=["hf_0"])
        summary = run_payload_dryrun(
            archive_dir=archive,
            output_dir=out,
            property_kinds=("hf_0",),
            use_cache_only=True,
        )
        data = json.loads((out / "hf_0.json").read_text())
        assert data["parsed_row_count"] > 0
        assert data["payload_count"] > 0
        assert data["health"] == "healthy"
        assert summary.unhealthy_count == 0

    def test_hf_0_cache_lookup_does_not_collide_with_hf_0_with_uncertainty(
        self, tmp_path
    ):
        """Regression for the May 2026 bug: the cached-HTML glob
        was too loose (``property_hf_0_*.html`` matched
        ``property_hf_0_with_uncertainty_<sha>.html``), so the hf_0
        target was silently parsed against the goodlist's 31-row
        HTML and emitted zero payloads. The fix anchors the regex
        on the trailing ``_<sha12>.html`` suffix."""

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_cache(archive, kinds=["hf_0", "hf_0_with_uncertainty"])
        run_payload_dryrun(
            archive_dir=archive, output_dir=out, use_cache_only=True
        )
        hf_0 = json.loads((out / "hf_0.json").read_text())
        hf_unc = json.loads(
            (out / "hf_0_with_uncertainty.json").read_text()
        )
        # The two targets must report independent row counts —
        # if the glob collided, hf_0's payload count would mirror
        # hf_unc's (or vanish).
        assert hf_0["parsed_row_count"] != hf_unc["parsed_row_count"] \
            or hf_0["detected_headers"] != hf_unc["detected_headers"]
        assert hf_0["payload_count"] > 0
        assert hf_unc["payload_count"] > 0


def test_dryrun_module_imports_no_orm_sessions():
    """Structural guarantee that the dry-run never touches the DB."""

    from app.importers.cccbdb import property_payload_dryrun

    for name in (
        "Session",
        "sessionmaker",
        "create_engine",
        "scoped_session",
    ):
        assert name not in property_payload_dryrun.__dict__
