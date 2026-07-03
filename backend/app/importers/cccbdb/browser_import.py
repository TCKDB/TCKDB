"""Browser-assisted import for CCCBDB per-species all-data pages.

Why this exists
---------------

CCCBDB's scripted-fetch surface is unreliable:

* ``alldata2x.asp?casno=...`` sometimes returns the formula-entry
  form instead of species data (the failure mode the Phase 5b
  classifier now rejects).
* ``exp1x.asp`` / ``alldata1x.asp`` are themselves the formula-entry
  pages.
* ``getformx.asp`` is the real form workflow, but Python ``requests``
  appears to trigger Cloudflare rate-limit / bot-detection
  behavior — the live diagnostic showed every form-POST strategy
  classifying as ``rate_limit_or_error_page``.

A human in a browser, however, gets a real per-species page just fine.
So the pragmatic path is: let a maintainer save the rendered HTML from
their browser, and import that local file into the standard CCCBDB
archive layout, gated by the same hardened classifier.

What this module does
---------------------

* Takes a local HTML file as input — never touches the network.
* Classifies it with :func:`classify_html`. Only ``molecule_data_page``
  responses are accepted as species all-data snapshots by default.
* Copies accepted HTML into ``raw_html/species_alldata_<key>_<sha>.html``.
* Runs :func:`parse_species_all_data_page` and writes parsed JSON.
* Merges a manifest record into ``manifest.json`` — additive, dedupes
  on ``(species_key, page_kind, content_sha256)`` so re-importing the
  exact same content is a no-op.

What it does not do
-------------------

* No browser automation. The maintainer uses their actual browser.
* No network fetches.
* No DB writes.
* No new schema. The manifest record reuses the
  :class:`RecordResult` shape so importers and the regular snapshot
  runner can interleave records in the same manifest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.diagnostics.classifier import (
    Classification,
    classify_html,
)
from app.importers.cccbdb.parsers import parse_species_all_data_page
from app.importers.cccbdb.snapshot import (
    BUILDER_VERSION,
    SNAPSHOT_VERSION,
    RecordResult,
)

DEFAULT_RESOLVER_STRATEGY = "browser_saved_html"


@dataclass
class BrowserImportConfig:
    """Inputs for one browser-saved-page import.

    :param input_html_path: Local file the user saved from a browser.
    :param output_dir: CCCBDB archive root (same as ``--output-dir``
        on the snapshot CLI).
    :param species_key: Filesystem-safe identifier, e.g. ``"h2o"``.
    :param source_url: The URL the maintainer was looking at when
        they saved the page. Required for provenance even though the
        HTML came from disk.
    :param cas_number: CAS Registry Number (digits only). Optional
        but recommended.
    :param resolver_strategy: Defaults to ``"browser_saved_html"``;
        callers may override with a more specific token if they
        used (e.g.) a session-aware curl recipe.
    :param allow_unknown: When ``True``, ``Classification.unknown``
        responses are accepted as data (a deliberate escape hatch
        for pages that lack the strict identifier patterns the
        Phase 5b classifier requires).
    :param save_rejected_html: When ``True``, rejected pages are
        copied to ``rejected_html/`` for forensic inspection.
    :param note: Free-text note recorded on the manifest record.
    """

    input_html_path: Path
    output_dir: Path
    species_key: str
    source_url: str
    cas_number: str | None = None
    resolver_strategy: str = DEFAULT_RESOLVER_STRATEGY
    allow_unknown: bool = False
    save_rejected_html: bool = False
    note: str | None = None


@dataclass
class BrowserImportResult:
    """Outcome of one import operation."""

    record: RecordResult
    manifest_path: Path
    manifest_record_count: int


def import_saved_species_page(
    config: BrowserImportConfig,
) -> BrowserImportResult:
    """Run the gate + import flow on one browser-saved HTML file.

    Never fetches the network. Idempotent on identical input HTML:
    re-importing the same SHA updates the existing manifest record
    in place rather than appending a duplicate.

    :raises FileNotFoundError: when ``input_html_path`` does not exist.
    :raises ValueError: when required metadata is missing.
    """

    if not config.species_key.strip():
        raise ValueError("species_key is required")
    if not config.source_url.strip():
        raise ValueError("source_url is required for provenance")
    if not config.input_html_path.exists():
        raise FileNotFoundError(
            f"input HTML not found: {config.input_html_path}"
        )

    html = config.input_html_path.read_text(encoding="utf-8")
    content_sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
    short = content_sha256[:12]
    base_name = f"species_alldata_{config.species_key}_{short}"

    archive_root = config.output_dir
    raw_dir = archive_root / "raw_html"
    parsed_dir = archive_root / "parsed"
    rejected_dir = archive_root / "rejected_html"
    manifest_path = archive_root / "manifest.json"

    archive_root.mkdir(parents=True, exist_ok=True)

    record = RecordResult(
        species_key=config.species_key,
        page_kind="species_all_data",
        source_url=config.source_url,
        source_record_key=config.species_key,
        retrieved_at=_now_utc_iso(),
        http_status=None,
        content_sha256=content_sha256,
        raw_html_path=None,
        parsed_json_path=None,
        payload_json_path=None,
        cache_hit=False,
        final_url=None,
        resolver_strategy=config.resolver_strategy,
    )

    # --- Classify ------------------------------------------------------
    # We don't know what URL the browser actually landed on, so
    # ``final_url`` is None — the classifier treats that as
    # "URL unchanged" for the formula-entry → redirect-landing branch.
    verdict = classify_html(
        html,
        attempted_url=config.source_url,
        final_url=None,
    )
    record.classification = verdict.classification.value
    record.classification_reason = verdict.reason

    accepted = _is_accepted(verdict.classification, config.allow_unknown)
    record.accepted_as_data = accepted

    if not accepted:
        record.resolver_warnings.append(
            f"rejected by classification gate: "
            f"{verdict.classification.value} ({verdict.reason})"
        )
        if config.save_rejected_html:
            rejected_dir.mkdir(parents=True, exist_ok=True)
            rejected_path = rejected_dir / f"{base_name}.html"
            rejected_path.write_text(html, encoding="utf-8")
            record.rejected_html_path = str(
                rejected_path.relative_to(archive_root)
            )
        manifest = _merge_manifest(
            manifest_path,
            record,
            extra_record_fields={
                "cas_number": config.cas_number,
                "note": config.note,
            },
        )
        return BrowserImportResult(
            record=record,
            manifest_path=manifest_path,
            manifest_record_count=len(manifest["records"]),
        )

    # --- Accepted path: write raw, parse, write parsed -----------------
    raw_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{base_name}.html"
    parsed_path = parsed_dir / f"{base_name}.json"

    raw_path.write_text(html, encoding="utf-8")
    record.raw_html_path = str(raw_path.relative_to(archive_root))

    parsed_record = parse_species_all_data_page(
        html,
        source_url=config.source_url,
        cas_number=config.cas_number,
        source_record_key=config.species_key,
    )
    record.parser_warnings = list(parsed_record.warnings)

    parsed_dump = parsed_record.model_dump(mode="json")
    parsed_path.write_text(
        json.dumps(parsed_dump, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    record.parsed_json_path = str(parsed_path.relative_to(archive_root))

    manifest = _merge_manifest(
        manifest_path,
        record,
        extra_record_fields={
            "cas_number": config.cas_number,
            "note": config.note,
        },
    )
    return BrowserImportResult(
        record=record,
        manifest_path=manifest_path,
        manifest_record_count=len(manifest["records"]),
    )


def _is_accepted(
    classification: Classification, allow_unknown: bool
) -> bool:
    """Phase 5a/5b gate: only ``molecule_data_page`` is accepted by
    default. ``unknown`` is opt-in (the browser-saved corpus may
    occasionally surface pages that lack the strict identifier
    patterns the classifier requires)."""

    if classification == Classification.molecule_data_page:
        return True
    if classification == Classification.unknown and allow_unknown:
        return True
    return False


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_manifest(
    manifest_path: Path,
    record: RecordResult,
    *,
    extra_record_fields: dict[str, Any],
) -> dict[str, Any]:
    """Append-or-replace one manifest record.

    Records are deduped on ``(species_key, page_kind, content_sha256)``:
    re-importing the exact same content updates the existing record
    in place; new content for the same species creates an additional
    record alongside.
    """

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "source": SOURCE_NAME,
            "source_release": SOURCE_RELEASE,
            "source_database_doi": SOURCE_DATABASE_DOI,
            "snapshot_version": SNAPSHOT_VERSION,
            "created_at": _now_utc_iso(),
            "parser_version": PARSER_VERSION,
            "builder_version": BUILDER_VERSION,
            "records": [],
        }

    record_dict = record.to_manifest()
    record_dict.update(extra_record_fields)

    dedupe_key = (
        record_dict["species_key"],
        record_dict["page_kind"],
        record_dict["content_sha256"],
    )
    updated = False
    for i, existing in enumerate(manifest["records"]):
        existing_key = (
            existing.get("species_key"),
            existing.get("page_kind"),
            existing.get("content_sha256"),
        )
        if existing_key == dedupe_key:
            manifest["records"][i] = record_dict
            updated = True
            break
    if not updated:
        manifest["records"].append(record_dict)

    manifest["last_modified_at"] = _now_utc_iso()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return manifest


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI shim
    """CLI entry point. Returns a process exit code.

    Exit codes:
      * 0 — accepted as data, archive updated.
      * 1 — rejected by classifier; archive not updated (or only
        ``rejected_html/`` updated when ``--save-rejected-html``).
      * 2 — missing input / argument validation error.
    """

    import argparse
    import logging

    parser = argparse.ArgumentParser(
        prog="cccbdb_import_saved_species_page",
        description=(
            "Import a CCCBDB per-species page that was saved from a "
            "browser. Never fetches the network."
        ),
    )
    parser.add_argument(
        "--input-html", type=Path, required=True,
        help="Local HTML file saved from the browser.",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="CCCBDB archive root (same as snapshot --output-dir).",
    )
    parser.add_argument("--species-key", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--cas-number", default=None)
    parser.add_argument(
        "--resolver-strategy", default=DEFAULT_RESOLVER_STRATEGY
    )
    parser.add_argument("--allow-unknown", action="store_true")
    parser.add_argument("--save-rejected-html", action="store_true")
    parser.add_argument("--note", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    try:
        config = BrowserImportConfig(
            input_html_path=args.input_html,
            output_dir=args.output_dir,
            species_key=args.species_key,
            source_url=args.source_url,
            cas_number=args.cas_number,
            resolver_strategy=args.resolver_strategy,
            allow_unknown=args.allow_unknown,
            save_rejected_html=args.save_rejected_html,
            note=args.note,
        )
        result = import_saved_species_page(config)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 2

    rec = result.record
    logger.info(
        "%s: classification=%s accepted=%s raw_html=%s parsed_json=%s",
        rec.species_key,
        rec.classification,
        rec.accepted_as_data,
        rec.raw_html_path,
        rec.parsed_json_path,
    )
    logger.info("manifest: %s (%d record(s))", result.manifest_path, result.manifest_record_count)
    return 0 if rec.accepted_as_data else 1
