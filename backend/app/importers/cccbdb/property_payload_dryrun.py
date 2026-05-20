"""CCCBDB property-table dry-run payload exporter.

Composes the Phase 5c façade (``PropertyTableFetcher``,
``PropertyTableParser``, ``PropertyTableIngestor``) end-to-end and
emits one JSON file per property target plus an aggregate
``summary.json``. **Never writes to the database.** **Never fetches
the network when ``use_cache_only=True``.**

The goal is to *prove the pipeline*: every accepted CCCBDB row
becomes a ``MolecularPropertyObservationCreate`` payload that
round-trips through ``model_dump(mode="json")`` → ``model_validate``.
A row that fails validation contributes a warning to its target's
per-row warning list but does not abort the rest of the run — that
keeps the dry-run useful as a diagnostic even when a single property
table has a column drift.

Output layout (when called with ``output_dir = .../payloads_dryrun``)::

    payloads_dryrun/
      summary.json
      hf_0.json
      hf_0_with_uncertainty.json
      dipole.json
      diatomic_spectroscopic.json
      polarizability_iso.json

Each per-target file is the shape:

.. code-block:: json

    {
      "property_kind": "...",
      "source_url": "...",
      "detected_headers": ["..."],
      "parsed_row_count": 0,
      "payload_count": 0,
      "invalid_payload_count": 0,
      "warning_count": 0,
      "skipped_missing_cache": false,
      "warnings": [],
      "payloads": []
    }

``summary.json`` aggregates across all targets — counts plus a
``per_target`` array and a ``warning_summary`` map for at-a-glance
inspection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.importers.cccbdb.crawl_plan import (
    EXPERIMENTAL_PROPERTIES_PILOT,
    CrawlTarget,
)
from app.importers.cccbdb.models import CCCBDBMoleculeCatalog
from app.importers.cccbdb.parsers import parse_molecule_catalog_page
from app.importers.cccbdb.property_table_ingest import (
    PropertyTableFetcher,
    PropertyTableIngestor,
    PropertyTableParser,
)
from app.importers.cccbdb.snapshot import Fetcher, HttpFetcher
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
)

_logger = logging.getLogger(__name__)


@dataclass
class TargetDryRunResult:
    """One property-table target's dry-run outcome."""

    property_kind: str
    source_url: str
    detected_headers: list[str] = field(default_factory=list)
    parsed_row_count: int = 0
    payload_count: int = 0
    invalid_payload_count: int = 0
    warning_count: int = 0
    skipped_missing_cache: bool = False
    warnings: list[str] = field(default_factory=list)
    payloads: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "property_kind": self.property_kind,
            "source_url": self.source_url,
            "detected_headers": list(self.detected_headers),
            "parsed_row_count": self.parsed_row_count,
            "payload_count": self.payload_count,
            "invalid_payload_count": self.invalid_payload_count,
            "warning_count": self.warning_count,
            "skipped_missing_cache": self.skipped_missing_cache,
            "warnings": list(self.warnings),
            "payloads": list(self.payloads),
        }


@dataclass
class DryRunSummary:
    """Aggregate dry-run report covering every target."""

    target_count: int = 0
    total_payload_count: int = 0
    total_invalid_payload_count: int = 0
    total_warning_count: int = 0
    skipped_count: int = 0
    use_cache_only: bool = False
    created_at: str = ""
    per_target: list[dict[str, Any]] = field(default_factory=list)
    warning_summary: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "use_cache_only": self.use_cache_only,
            "target_count": self.target_count,
            "total_payload_count": self.total_payload_count,
            "total_invalid_payload_count": self.total_invalid_payload_count,
            "total_warning_count": self.total_warning_count,
            "skipped_count": self.skipped_count,
            "per_target": list(self.per_target),
            "warning_summary": dict(self.warning_summary),
        }


def _select_targets(
    property_kinds: tuple[str, ...] | None,
) -> tuple[CrawlTarget, ...]:
    """Filter ``EXPERIMENTAL_PROPERTIES_PILOT`` by ``property_kinds``,
    preserving the pilot's declaration order. ``None`` selects all.

    :raises ValueError: if a requested kind is not in the pilot.
    """

    if property_kinds is None:
        return EXPERIMENTAL_PROPERTIES_PILOT
    pilot_by_kind = {t.property_kind: t for t in EXPERIMENTAL_PROPERTIES_PILOT}
    unknown = [k for k in property_kinds if k not in pilot_by_kind]
    if unknown:
        raise ValueError(
            f"unknown property_kind(s): {unknown!r}. "
            f"Allowed: {sorted(pilot_by_kind.keys())!r}"
        )
    return tuple(pilot_by_kind[k] for k in property_kinds)


def _load_catalog(
    catalog_html_path: Path | None,
) -> CCCBDBMoleculeCatalog | None:
    if catalog_html_path is None:
        return None
    if not catalog_html_path.exists():
        _logger.warning(
            "catalog HTML not found at %s; running without identity enrichment",
            catalog_html_path,
        )
        return None
    return parse_molecule_catalog_page(
        catalog_html_path.read_text(encoding="utf-8"),
        source_url="https://cccbdb.nist.gov/inchix.asp",
    )


def run_payload_dryrun(
    *,
    archive_dir: Path,
    output_dir: Path,
    property_kinds: tuple[str, ...] | None = None,
    use_cache_only: bool = False,
    sleep_seconds: float = 5.0,
    fetcher: Fetcher | None = None,
    catalog: CCCBDBMoleculeCatalog | None = None,
    catalog_html_path: Path | None = None,
) -> DryRunSummary:
    """Run the dry-run exporter and write JSON files under ``output_dir``.

    :param archive_dir: CCCBDB archive root (the snapshot
        ``--output-dir``). Must already contain ``raw_html/`` for
        cache-only runs.
    :param output_dir: Where per-target JSON + summary.json land.
    :param property_kinds: Restrict to a subset of the pilot. ``None``
        runs every target in ``EXPERIMENTAL_PROPERTIES_PILOT``.
    :param use_cache_only: When ``True``, the runner refuses to make
        network requests. Targets without cached HTML are recorded
        as ``skipped_missing_cache=True``.
    :param sleep_seconds: Polite delay between live fetches. Ignored
        when ``use_cache_only=True``.
    :param fetcher: Override the underlying :class:`Fetcher`. Tests
        inject a fake; production uses :class:`HttpFetcher`.
    :param catalog: Pre-parsed catalog for identity enrichment.
        Mutually-exclusive with ``catalog_html_path``.
    :param catalog_html_path: Path to a saved ``inchix.asp`` HTML
        snapshot. Loaded into a catalog object if present.
    :returns: A :class:`DryRunSummary` with aggregate counts. Per-row
        validation failures contribute warnings, never exceptions.
    :raises ValueError: when ``use_cache_only=True`` and
        ``archive_dir`` does not exist (no cache to read from).
    """

    targets = _select_targets(property_kinds)
    if use_cache_only and not archive_dir.exists():
        raise ValueError(
            f"--use-cache-only set but archive dir does not exist: "
            f"{archive_dir}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    if catalog is None and catalog_html_path is not None:
        catalog = _load_catalog(catalog_html_path)

    fetcher_obj = PropertyTableFetcher(
        output_dir=archive_dir,
        fetcher=fetcher,
        sleep_seconds=sleep_seconds,
    )
    parser_obj = PropertyTableParser()
    ingestor_obj = PropertyTableIngestor(catalog=catalog)

    # If not cache-only, populate the cache by running the underlying
    # snapshot. This is the same code path Phase 5c's
    # ``ingest_property_pilot`` uses; the wrapper exists so the
    # cache-only branch can skip it cleanly.
    if not use_cache_only:
        _logger.info(
            "Fetching %d target(s) via snapshot runner (sleep=%.1fs)...",
            len(targets),
            sleep_seconds,
        )
        fetcher_obj.fetch_targets(targets)

    summary = DryRunSummary(
        use_cache_only=use_cache_only,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    for target in targets:
        result = _run_one_target(
            target=target,
            archive_dir=archive_dir,
            output_dir=output_dir,
            fetcher_obj=fetcher_obj,
            parser_obj=parser_obj,
            ingestor_obj=ingestor_obj,
            use_cache_only=use_cache_only,
        )
        summary.per_target.append(result.to_json())
        summary.target_count += 1
        summary.total_payload_count += result.payload_count
        summary.total_invalid_payload_count += result.invalid_payload_count
        summary.total_warning_count += result.warning_count
        if result.skipped_missing_cache:
            summary.skipped_count += 1
        summary.warning_summary[result.property_kind] = result.warning_count

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _run_one_target(
    *,
    target: CrawlTarget,
    archive_dir: Path,
    output_dir: Path,
    fetcher_obj: PropertyTableFetcher,
    parser_obj: PropertyTableParser,
    ingestor_obj: PropertyTableIngestor,
    use_cache_only: bool,
) -> TargetDryRunResult:
    property_kind = target.property_kind or target.species_key
    result = TargetDryRunResult(
        property_kind=property_kind,
        source_url=target.source_url,
    )

    html = fetcher_obj.load_cached_html(target)
    if html is None:
        if use_cache_only:
            result.skipped_missing_cache = True
            result.warnings.append(
                "skipped_missing_cache: no raw HTML in archive and "
                "--use-cache-only is set"
            )
            result.warning_count = len(result.warnings)
            _write_target_json(output_dir, result)
            return result
        # Non-cache-only with no HTML usually means the snapshot
        # fetch was rejected (e.g. classifier gate or fetch error).
        result.warnings.append(
            "no cached HTML even after fetch attempt; see manifest.json"
        )
        result.warning_count = len(result.warnings)
        _write_target_json(output_dir, result)
        return result

    try:
        table = parser_obj.parse(target, html)
    except Exception as exc:  # noqa: BLE001 - keep going across targets
        result.warnings.append(f"parser_error: {type(exc).__name__}: {exc}")
        result.warning_count = len(result.warnings)
        _write_target_json(output_dir, result)
        return result

    result.detected_headers = list(table.column_names)
    result.parsed_row_count = len(table.rows)
    result.warnings.extend(table.warnings)

    build_results = ingestor_obj.to_payloads(table)
    for build_result in build_results:
        if build_result.payload is None:
            result.warnings.extend(
                f"row {build_result.row_index}: {w}"
                for w in build_result.warnings
            )
            continue
        # Round-trip validation: model_dump -> model_validate. This
        # catches Pydantic-encoder drift the same way Phase 2c's
        # round-trip catches builder/schema drift on disk.
        try:
            payload_json = build_result.payload.model_dump(mode="json")
            MolecularPropertyObservationCreate.model_validate(payload_json)
        except ValidationError as exc:
            result.invalid_payload_count += 1
            result.warnings.append(
                f"row {build_result.row_index}: invalid payload "
                f"({type(exc).__name__}: "
                f"{exc.errors()[0].get('msg', '<no msg>')!r})"
            )
            continue
        result.payloads.append(payload_json)
        result.payload_count += 1
        # Per-row warnings (catalog ambiguity, etc.) flow through to
        # the target-level warnings list for at-a-glance visibility.
        result.warnings.extend(
            f"row {build_result.row_index}: {w}"
            for w in build_result.warnings
        )

    result.warning_count = len(result.warnings)
    _write_target_json(output_dir, result)
    return result


def _write_target_json(
    output_dir: Path, result: TargetDryRunResult
) -> Path:
    path = output_dir / f"{result.property_kind}.json"
    path.write_text(
        json.dumps(result.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_arg_parser():  # pragma: no cover - thin argparse wrapping
    import argparse

    p = argparse.ArgumentParser(
        prog="cccbdb_property_payload_dryrun",
        description=(
            "Run the CCCBDB cross-species property-table ingestion "
            "pipeline end-to-end and export dry-run "
            "MolecularPropertyObservationCreate payloads as JSON. "
            "Never writes to the database."
        ),
    )
    p.add_argument(
        "--archive-dir", type=Path, required=True,
        help="CCCBDB archive root (the snapshot --output-dir).",
    )
    p.add_argument(
        "--output-dir", type=Path, required=True,
        help="Where to write per-target JSON + summary.json.",
    )
    p.add_argument(
        "--property-kind",
        action="append",
        default=None,
        help=(
            "Restrict to a single property_kind from "
            "EXPERIMENTAL_PROPERTIES_PILOT. May be repeated. "
            "Defaults to every target in the pilot."
        ),
    )
    p.add_argument("--use-cache-only", action="store_true")
    p.add_argument("--sleep-seconds", type=float, default=5.0)
    p.add_argument(
        "--catalog-html",
        type=Path,
        default=None,
        help=(
            "Optional path to a saved inchix.asp HTML snapshot. When "
            "supplied, payloads get catalog identity hints."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code.

    Exit codes:
      * 0 — dry-run finished. Per-row parser warnings and invalid
        payloads do NOT cause a nonzero exit; inspect summary.json.
      * 2 — argument / configuration error (missing archive dir,
        unknown property_kind, etc.).
    """

    import sys

    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    property_kinds = (
        tuple(args.property_kind) if args.property_kind else None
    )

    try:
        summary = run_payload_dryrun(
            archive_dir=args.archive_dir,
            output_dir=args.output_dir,
            property_kinds=property_kinds,
            use_cache_only=args.use_cache_only,
            sleep_seconds=args.sleep_seconds,
            catalog_html_path=args.catalog_html,
        )
    except ValueError as exc:
        _logger.error("%s", exc)
        return 2

    _logger.info(
        "Dry-run complete: %d target(s), %d payloads, %d invalid, %d skipped",
        summary.target_count,
        summary.total_payload_count,
        summary.total_invalid_payload_count,
        summary.skipped_count,
    )
    _logger.info("Wrote summary to %s", args.output_dir / "summary.json")
    return 0
