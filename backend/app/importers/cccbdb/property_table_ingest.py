"""High-level façade for CCCBDB cross-species property-table ingestion.

This module gives the existing primitives — the parser
(``parse_experimental_property_table_page``), the builder
(``build_molecular_property_payloads_from_property_table``), the
snapshot runner, the catalog enrichment helper, the
``MolecularPropertyObservation`` model — a clean class-shape API
that fits the Phase 5c prompt:

    PropertyTableFetcher.fetch_or_use_cache(target)
        ─→ raw HTML (plus a manifest-shaped RecordResult)
    PropertyTableParser.parse(target, html)
        ─→ CCCBDBExperimentalPropertyTable
    PropertyTableIngestor.to_payloads(table)
        ─→ list[CCCBDBMolecularPropertyBuildResult]
            each .payload is a MolecularPropertyObservationCreate

Nothing here re-implements the underlying logic. The façade exists so
ingestion code can compose the three steps as separate objects with
their own injection seams (a test can swap the fetcher's transport
without touching the parser or the ingestor, etc.).

The composition that ties all three together for the four allowlisted
property tables — Hf(0K), Goodlist Hf(0K) + unc, dipole, diatomic
spectroscopic, and now polarizability — is :func:`ingest_property_pilot`.

Policy this façade enforces (matches the rest of the importer):

* No DB writes. Output is ``MolecularPropertyObservationCreate``
  payloads; persistence is a separate workflow layer.
* No fetching of per-species pages. The crawl plan is restricted to
  ``experimental_property_table`` targets.
* Catalog identity enrichment is *hint-only*: the ingestor surfaces
  the matched InChI / InChIKey / SMILES via the existing
  ``raw_payload_json["identity_hint"]`` channel but never populates
  ``species_entry_id``. Translating an InChIKey into a real
  ``species_entry_id`` is the workflow layer's job, gated on
  dedup against existing rows.
* Ambiguous catalog matches (e.g. C2H6O — ethanol *or* dimethyl
  ether) never produce an identity hint; all candidates are
  preserved as ``raw_payload_json["catalog_candidates"]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.importers.cccbdb.builders import (
    CCCBDBMolecularPropertyBuildResult,
    build_molecular_property_payloads_from_property_table,
)
from app.importers.cccbdb.crawl_plan import (
    EXPERIMENTAL_PROPERTIES_PILOT,
    CATALOG_PILOT,
    CrawlTarget,
)
from app.importers.cccbdb.models import (
    CCCBDBExperimentalPropertyTable,
    CCCBDBMoleculeCatalog,
)
from app.importers.cccbdb.parsers import (
    PROPERTY_CONFIGS,
    parse_experimental_property_table_page,
    parse_molecule_catalog_page,
)
from app.importers.cccbdb.snapshot import (
    Fetcher,
    HttpFetcher,
    RecordResult,
    SnapshotConfig,
    run_snapshot,
)


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


@dataclass
class PropertyTableFetcher:
    """Polite cache-first fetcher for one or more property-table targets.

    Delegates to :func:`run_snapshot` so the cache-first / dry-run /
    Cloudflare-aware / manifest-update behavior already battle-tested
    in :mod:`app.importers.cccbdb.snapshot` is reused verbatim.

    :param output_dir: CCCBDB archive root. Raw HTML is written under
        ``raw_html/property_<key>_<sha>.html``; parsed JSON under
        ``parsed/property_<key>_<sha>.json``.
    :param fetcher: Underlying :class:`Fetcher` callable. Production
        callers pass :class:`HttpFetcher`; tests inject a fake.
    :param sleep_seconds: Polite delay between requests. Defaults to
        5 seconds (CCCBDB rate-limits aggressively; the Phase 2b runner
        used 2s and that proved too tight in some live runs).
    :param dry_run: When ``True``, no files are written and the live
        fetcher is not called — cached pages are still served.
    :param force_refresh: When ``True``, ignore the cache and re-fetch.
    """

    output_dir: Path
    fetcher: Fetcher | None = None
    sleep_seconds: float = 5.0
    dry_run: bool = False
    force_refresh: bool = False

    def fetch_targets(
        self, targets: tuple[CrawlTarget, ...]
    ) -> dict[str, Any]:
        """Fetch every target via :func:`run_snapshot` and return the
        manifest dict that was (or would have been, if dry-run)
        written. Caching, Cloudflare-aware retries, and rate-limit
        handling come from the underlying runner."""

        config = SnapshotConfig(
            output_dir=self.output_dir,
            fetcher=self.fetcher or HttpFetcher(),
            sleep_seconds=self.sleep_seconds,
            dry_run=self.dry_run,
            force_refresh=self.force_refresh,
            # Property tables don't build payloads from snapshot
            # (Phase 4a routes those through the Ingestor below).
            write_payloads=False,
        )
        return run_snapshot(targets, config)

    def load_cached_html(self, target: CrawlTarget) -> str | None:
        """Return the most recent cached raw HTML for a target, or
        ``None`` when nothing is cached. Useful for one-off ingestion
        runs that don't want to re-invoke :func:`run_snapshot`."""

        from app.importers.cccbdb.snapshot import _find_cached_raw_html

        raw_dir = self.output_dir / "raw_html"
        if not raw_dir.exists():
            return None
        cached = _find_cached_raw_html(
            raw_dir, target.species_key, target.page_kind
        )
        if cached is None:
            return None
        path, _sha = cached
        return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@dataclass
class PropertyTableParser:
    """Thin façade over :func:`parse_experimental_property_table_page`.

    Exists so ingestion code can hold a single parser instance,
    swap implementations in tests if ever needed, and make the
    parsed-table shape obvious in type hints.
    """

    def parse(
        self,
        target: CrawlTarget,
        html: str,
    ) -> CCCBDBExperimentalPropertyTable:
        """Parse one already-fetched property-table HTML body.

        :raises ValueError: if ``target.property_kind`` is ``None``
            or not registered in :data:`PROPERTY_CONFIGS`.
        """

        if target.property_kind is None:
            raise ValueError(
                "property_kind is required for experimental_property_table "
                "targets"
            )
        if target.property_kind not in PROPERTY_CONFIGS:
            raise ValueError(
                f"property_kind {target.property_kind!r} is not registered; "
                f"add a PropertyTableConfig entry first"
            )
        return parse_experimental_property_table_page(
            html,
            property_kind=target.property_kind,
            source_url=target.source_url,
            source_record_key=target.species_key,
        )

    def known_property_kinds(self) -> list[str]:
        """Return the list of currently-registered property-table
        ``property_kind`` tokens, in alphabetical order."""

        return sorted(PROPERTY_CONFIGS.keys())


# ---------------------------------------------------------------------------
# Ingestor
# ---------------------------------------------------------------------------


@dataclass
class PropertyTableIngestor:
    """Convert one parsed property table into upload-ready payloads.

    Wraps :func:`build_molecular_property_payloads_from_property_table`
    so callers can pass a pre-loaded :class:`CCCBDBMoleculeCatalog`
    once and reuse it across many tables (catalog parsing is cheap
    but not free).

    :param catalog: Optional pre-parsed catalog. When supplied,
        identity-enrichment candidates are surfaced on each row via
        ``raw_payload_json["identity_hint"]`` (unambiguous matches
        only). When ``None``, no enrichment runs.
    """

    catalog: CCCBDBMoleculeCatalog | None = None

    @classmethod
    def with_catalog_html(cls, catalog_html: str) -> "PropertyTableIngestor":
        """Build an ingestor by parsing one CCCBDB ``inchix.asp``
        catalog HTML body. Convenience constructor for the common
        case where the maintainer already has a saved catalog."""

        catalog = parse_molecule_catalog_page(
            catalog_html,
            source_url="https://cccbdb.nist.gov/inchix.asp",
        )
        return cls(catalog=catalog)

    def to_payloads(
        self, table: CCCBDBExperimentalPropertyTable
    ) -> list[CCCBDBMolecularPropertyBuildResult]:
        """Return one :class:`CCCBDBMolecularPropertyBuildResult` per
        parsed row. Each ``result.payload`` validates against the real
        :class:`MolecularPropertyObservationCreate` schema and is
        ready for the workflow layer to persist (or to dump to JSON
        for offline inspection)."""

        return build_molecular_property_payloads_from_property_table(
            table, catalog=self.catalog
        )


# ---------------------------------------------------------------------------
# Top-level pilot orchestration
# ---------------------------------------------------------------------------


@dataclass
class PilotIngestionResult:
    """Aggregate output of :func:`ingest_property_pilot`.

    ``per_target`` maps each target's ``property_kind`` to its list of
    builder results. ``manifest`` is the manifest dict written by the
    underlying snapshot run.
    """

    manifest: dict[str, Any]
    per_target: dict[str, list[CCCBDBMolecularPropertyBuildResult]] = field(
        default_factory=dict
    )

    def workflow_ready_count(self) -> int:
        """Total rows across all targets that produced a workflow-ready
        :class:`MolecularPropertyObservationCreate`."""

        return sum(
            1
            for results in self.per_target.values()
            for r in results
            if r.payload is not None
        )

    def warning_summary(self) -> dict[str, int]:
        """Aggregate warning counts per property_kind."""

        return {
            kind: sum(len(r.warnings) for r in results)
            for kind, results in self.per_target.items()
        }


def ingest_property_pilot(
    output_dir: Path,
    *,
    fetcher: Fetcher | None = None,
    catalog: CCCBDBMoleculeCatalog | None = None,
    sleep_seconds: float = 5.0,
    dry_run: bool = False,
    targets: tuple[CrawlTarget, ...] = EXPERIMENTAL_PROPERTIES_PILOT,
) -> PilotIngestionResult:
    """Fetch + parse + ingest the full property-table pilot.

    The three-step composition that turns a fresh archive directory
    into a list of workflow-ready upload payloads:

    1. :class:`PropertyTableFetcher` fetches every target (using cache
       when present) and writes ``raw_html/`` + ``parsed/`` + manifest.
    2. :class:`PropertyTableParser` re-parses each saved table from
       the cached raw HTML (so the same parse runs in the same
       environment regardless of when fetching happened).
    3. :class:`PropertyTableIngestor` converts each table into
       per-row :class:`MolecularPropertyObservationCreate` payloads,
       optionally enriched with catalog identity hints.

    No DB writes. No live fetches if every target's HTML is already
    cached. The function returns the aggregate
    :class:`PilotIngestionResult` so callers can inspect verdicts,
    warnings, and row counts before deciding how to consume the
    payloads.
    """

    fetcher_obj = PropertyTableFetcher(
        output_dir=output_dir,
        fetcher=fetcher,
        sleep_seconds=sleep_seconds,
        dry_run=dry_run,
    )
    manifest = fetcher_obj.fetch_targets(targets)

    parser_obj = PropertyTableParser()
    ingestor_obj = PropertyTableIngestor(catalog=catalog)

    per_target: dict[str, list[CCCBDBMolecularPropertyBuildResult]] = {}
    for target in targets:
        html = fetcher_obj.load_cached_html(target)
        if html is None:
            # Either dry-run with cold cache, or the fetch was rejected
            # / failed. The manifest still records what happened.
            continue
        table = parser_obj.parse(target, html)
        per_target[target.property_kind or target.species_key] = (
            ingestor_obj.to_payloads(table)
        )

    return PilotIngestionResult(
        manifest=manifest,
        per_target=per_target,
    )


__all__ = [
    "PilotIngestionResult",
    "PropertyTableFetcher",
    "PropertyTableIngestor",
    "PropertyTableParser",
    "ingest_property_pilot",
    "CATALOG_PILOT",  # re-exported for caller convenience
]
