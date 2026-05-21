"""Tests for the property-table ingestion façade classes + the
polarizability_iso property kind added in Phase 5c.

The façades wrap existing primitives (parser, builder, runner) so the
heavy correctness tests live under their respective files:

* parsing → ``test_property_table_parser.py``
* building → ``test_molecular_property_builder.py``
* snapshot caching → ``test_snapshot.py``

This file's job is to pin the *composition* contract: the three
classes plug together correctly, the new polarizability kind round-
trips through the parser and ingestor, the pilot orchestrator
returns the expected aggregate shape, and the no-DB / no-network
policies still hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.db.models.common import MolecularPropertyKind
from app.importers.cccbdb.crawl_plan import (
    EXPERIMENTAL_PROPERTIES_PILOT,
    CrawlTarget,
)
from app.importers.cccbdb.models import (
    CCCBDBExperimentalPropertyTable,
    CCCBDBMoleculeCatalog,
)
from app.importers.cccbdb.parsers import parse_molecule_catalog_page
from app.importers.cccbdb.property_table_ingest import (
    PilotIngestionResult,
    PropertyTableFetcher,
    PropertyTableIngestor,
    PropertyTableParser,
    ingest_property_pilot,
)
from app.importers.cccbdb.snapshot import FetchResult

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)

_POL_URL = "https://cccbdb.nist.gov/pollistx.asp"
_HF_URL = "https://cccbdb.nist.gov/hf0kx.asp"
_GOODLIST_URL = "https://cccbdb.nist.gov/goodlistx.asp"
_DIPOLE_URL = "https://cccbdb.nist.gov/diplistx.asp"
_DIATOMIC_URL = "https://cccbdb.nist.gov/expdiatomicsx.asp"


@dataclass
class _FixtureFetcher:
    """Maps every property-table URL to its bundled fixture."""

    calls: list[str]

    @classmethod
    def make(cls) -> "_FixtureFetcher":
        return cls(calls=[])

    def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        mapping = {
            _HF_URL: "property_hf_0.html",
            _GOODLIST_URL: "property_hf_0_with_uncertainty.html",
            _DIPOLE_URL: "property_dipoles.html",
            _DIATOMIC_URL: "property_diatomic_spectroscopic.html",
            _POL_URL: "property_polarizability_iso.html",
        }
        if url not in mapping:
            return FetchResult(None, 404, f"unallowlisted URL {url}")
        text = (FIXTURES_DIR / mapping[url]).read_text(encoding="utf-8")
        return FetchResult(text, 200, None)


@pytest.fixture(scope="module")
def catalog() -> CCCBDBMoleculeCatalog:
    html = (FIXTURES_DIR / "catalog_inchix.html").read_text(encoding="utf-8")
    return parse_molecule_catalog_page(
        html, source_url="https://cccbdb.nist.gov/inchix.asp"
    )


# ---------------------------------------------------------------------------
# Polarizability_iso: parser + ingestor coverage
# ---------------------------------------------------------------------------


class TestPolarizabilityIsoTable:
    @pytest.fixture(scope="class")
    def table(self):
        html = (
            FIXTURES_DIR / "property_polarizability_iso.html"
        ).read_text(encoding="utf-8")
        target = next(
            t
            for t in EXPERIMENTAL_PROPERTIES_PILOT
            if t.property_kind == "polarizability_iso"
        )
        return PropertyTableParser().parse(target, html)

    def test_metadata(self, table: CCCBDBExperimentalPropertyTable):
        assert table.title == "Experimental Polarizabilities"
        assert table.raw_units == "Bohr^3"
        # ``polarizability_iso`` has dimension=None → raw unit passed
        # through as canonical (the property has no SI normalizer yet).
        assert table.canonical_unit == "Bohr^3"
        assert table.source_metadata.property_kind == "polarizability_iso"

    def test_isotropic_alpha_value_is_value_column(self, table):
        """Live pollistx.asp uses ``alpha`` (not ``iso``) for the
        isotropic polarizability."""

        h2o = next(
            r
            for r in table.rows
            if r.raw_row["Molecule"] == "H2O"
        )
        assert h2o.value == pytest.approx(9.90)
        assert h2o.unit == "Bohr^3"
        # The detected header is the live one, not the
        # previously-inferred xx/yy/zz/iso shape.
        assert "alpha" in table.column_names
        assert "xx" not in table.column_names

    def test_lih_row_has_comment_and_state(self, table):
        """LiH row carries a non-empty reference comment ('MB') and
        a populated electronic state. Both must survive the parse
        through the live-shape columns."""

        lih = next(
            r
            for r in table.rows
            if r.raw_row["Molecule"] == "LiH"
        )
        assert lih.value == pytest.approx(23.74)
        assert lih.reference is not None
        assert lih.reference.reference_comment == "MB"
        # ``State`` lookup is case-insensitive, so the row's
        # ``state_label_raw`` ends up populated from the ``State``
        # column even though the config spells it ``state_column="State"``.
        assert lih.state_label_raw is not None
        assert "Conformation" in table.column_names

    def test_full_pilot_includes_polarizability(self):
        kinds = {t.property_kind for t in EXPERIMENTAL_PROPERTIES_PILOT}
        assert "polarizability_iso" in kinds

    def test_ingestor_maps_polarizability_iso_to_distinct_enum_kind(
        self, table, catalog
    ):
        """``polarizability_iso`` is a separate enum value from
        ``polarizability`` (the latter is reserved for full tensor
        observations; the iso table is the trace/average). The
        builder's _PROPERTY_KIND_MAP must keep them distinct."""

        ingestor = PropertyTableIngestor(catalog=catalog)
        results = ingestor.to_payloads(table)
        for r in results:
            assert r.payload is not None
            assert (
                r.payload.property_kind
                == MolecularPropertyKind.polarizability_iso
            )
            # Defensive: never collapse to the tensor kind.
            assert (
                r.payload.property_kind != MolecularPropertyKind.polarizability
            )


# ---------------------------------------------------------------------------
# PropertyTableParser façade
# ---------------------------------------------------------------------------


class TestPropertyTableParser:
    def test_known_property_kinds_includes_new_polarizability(self):
        parser = PropertyTableParser()
        kinds = parser.known_property_kinds()
        # All Phase 3a + Phase 5c kinds should be visible to callers
        # without forcing them to import PROPERTY_CONFIGS directly.
        assert {
            "hf_0",
            "hf_0_with_uncertainty",
            "dipole",
            "diatomic_spectroscopic",
            "polarizability_iso",
        } <= set(kinds)

    def test_unknown_kind_rejected(self):
        target = CrawlTarget(
            species_key="x",
            source_url="https://example.invalid/x",
            page_kind="experimental_property_table",
            property_kind="not_a_real_kind",
            is_validated_url=True,
        )
        with pytest.raises(ValueError, match="not registered"):
            PropertyTableParser().parse(target, "<html></html>")

    def test_missing_property_kind_rejected(self):
        target = CrawlTarget(
            species_key="x",
            source_url="https://example.invalid/x",
            page_kind="experimental_property_table",
            property_kind=None,
            is_validated_url=True,
        )
        with pytest.raises(ValueError, match="property_kind is required"):
            PropertyTableParser().parse(target, "<html></html>")


# ---------------------------------------------------------------------------
# PropertyTableIngestor façade
# ---------------------------------------------------------------------------


class TestPropertyTableIngestor:
    def test_with_catalog_html_constructor(self):
        html = (FIXTURES_DIR / "catalog_inchix.html").read_text()
        ingestor = PropertyTableIngestor.with_catalog_html(html)
        assert ingestor.catalog is not None
        assert len(ingestor.catalog.entries) > 0

    def test_no_catalog_means_no_identity_hint(self):
        # Parse a real table.
        html = (FIXTURES_DIR / "property_dipoles.html").read_text()
        target = next(
            t for t in EXPERIMENTAL_PROPERTIES_PILOT if t.property_kind == "dipole"
        )
        table = PropertyTableParser().parse(target, html)
        # No catalog supplied → no identity_hint surfaced.
        results = PropertyTableIngestor(catalog=None).to_payloads(table)
        for r in results:
            assert r.identity_hint is None

    def test_unambiguous_catalog_match_surfaces_identity_hint(self, catalog):
        html = (FIXTURES_DIR / "property_dipoles.html").read_text()
        target = next(
            t for t in EXPERIMENTAL_PROPERTIES_PILOT if t.property_kind == "dipole"
        )
        table = PropertyTableParser().parse(target, html)
        results = PropertyTableIngestor(catalog=catalog).to_payloads(table)
        h2o = next(
            r
            for r in results
            if r.payload
            and r.payload.raw_payload_json["row_formula"] == "H2O"
        )
        assert h2o.identity_hint is not None
        assert h2o.identity_hint["inchikey"] == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"

    def test_species_entry_id_stays_null_even_on_unambiguous_match(
        self, catalog
    ):
        """The ingestor surfaces identity hints; it does NOT populate
        ``species_entry_id``. That requires a DB lookup the importer
        deliberately doesn't perform."""

        html = (FIXTURES_DIR / "property_dipoles.html").read_text()
        target = next(
            t for t in EXPERIMENTAL_PROPERTIES_PILOT if t.property_kind == "dipole"
        )
        table = PropertyTableParser().parse(target, html)
        results = PropertyTableIngestor(catalog=catalog).to_payloads(table)
        for r in results:
            if r.payload is not None:
                assert r.payload.species_entry_id is None


# ---------------------------------------------------------------------------
# Pilot orchestrator
# ---------------------------------------------------------------------------


class TestPilotIngestion:
    def test_end_to_end_pilot_run(self, tmp_path, catalog):
        fetcher = _FixtureFetcher.make()
        result = ingest_property_pilot(
            output_dir=tmp_path,
            fetcher=fetcher,
            catalog=catalog,
            sleep_seconds=0.0,
        )
        # All 5 targets fired through the fetcher (4 from Phase 3a +
        # polarizability from Phase 5c).
        assert len(fetcher.calls) == 5
        # And all 5 produced parsed tables + payloads.
        assert len(result.per_target) == 5
        # Aggregate workflow-ready count rolls up correctly.
        assert result.workflow_ready_count() > 0

    def test_manifest_records_every_target(self, tmp_path, catalog):
        result = ingest_property_pilot(
            output_dir=tmp_path,
            fetcher=_FixtureFetcher.make(),
            catalog=catalog,
            sleep_seconds=0.0,
        )
        records = result.manifest["records"]
        kinds = {
            r.get("page_kind") for r in records
        }
        assert kinds == {"experimental_property_table"}

    def test_warning_summary_is_per_target(self, tmp_path, catalog):
        result = ingest_property_pilot(
            output_dir=tmp_path,
            fetcher=_FixtureFetcher.make(),
            catalog=catalog,
            sleep_seconds=0.0,
        )
        summary = result.warning_summary()
        # Keys mirror the per-target dict.
        assert set(summary.keys()) == set(result.per_target.keys())

    def test_dry_run_with_cold_cache_returns_empty_per_target(self, tmp_path):
        """The orchestrator must not crash when ``dry_run=True`` and
        no cache exists — the fetch step writes no files, the
        load_cached_html step returns None for every target, so
        per_target ends up empty."""

        # Pass an exploding fetcher to prove no network is touched.
        def exploding(url):
            raise AssertionError("dry-run must not fetch")

        result = ingest_property_pilot(
            output_dir=tmp_path,
            fetcher=exploding,
            catalog=None,
            sleep_seconds=0.0,
            dry_run=True,
        )
        assert result.per_target == {}
        # Manifest is still produced (with fetch_warnings on every
        # record) so callers can see what would happen.
        assert len(result.manifest["records"]) == len(
            EXPERIMENTAL_PROPERTIES_PILOT
        )


# ---------------------------------------------------------------------------
# Structural no-DB / no-side-effect invariants
# ---------------------------------------------------------------------------


class TestNoDbWriteInvariants:
    def test_pilot_result_payloads_are_create_models_only(
        self, tmp_path, catalog
    ):
        """The ingestor returns Pydantic Create models, never ORM
        instances. If a future refactor accidentally smuggles a
        SQLAlchemy object into the payload list, this test fails."""

        from app.schemas.entities.molecular_property_observation import (
            MolecularPropertyObservationCreate,
        )

        result = ingest_property_pilot(
            output_dir=tmp_path,
            fetcher=_FixtureFetcher.make(),
            catalog=catalog,
            sleep_seconds=0.0,
        )
        for target_results in result.per_target.values():
            for r in target_results:
                if r.payload is not None:
                    assert isinstance(
                        r.payload, MolecularPropertyObservationCreate
                    )

    def test_property_table_ingest_module_imports_no_orm_sessions(self):
        """The façade module must not import SQLAlchemy sessions —
        that's a workflow-layer concern. The Create schema is fine
        (it's a Pydantic model)."""

        from app.importers.cccbdb import property_table_ingest

        for name in (
            "Session",
            "sessionmaker",
            "create_engine",
            "scoped_session",
        ):
            assert name not in property_table_ingest.__dict__
