"""CCCBDB snapshot archive.

Fetches an explicit allowlist of CCCBDB pages, saves the raw HTML by
content hash, runs the Phase 1 parser, optionally runs the Phase 2a
builder, and writes a deterministic ``manifest.json``. The archive is
the durable source of truth for CCCBDB-derived data: the website can
change or disappear and TCKDB payloads can still be regenerated from
the saved snapshots.

The runner uses a swappable :class:`Fetcher` callable so the test
suite can drive it with hand-written HTML rather than touching the
network. The default fetcher is :class:`HttpFetcher`, which uses
``requests`` with a polite User-Agent, short timeout, single retry,
and no parallelism.

Never writes to the database. Never crawls discovered links.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.crawl_plan import (
    EXPERIMENTAL_PILOT,
    PILOTS,
    CrawlTarget,
    UnverifiedUrlError,
    assert_all_validated,
)
from app.importers.cccbdb.diagnostics.classifier import (
    Classification,
    classify_html,
)
from app.importers.cccbdb.parsers import (
    parse_experimental_property_table_page,
    parse_experimental_species_page,
    parse_molecule_catalog_page,
    parse_species_all_data_page,
)

SNAPSHOT_VERSION = 1
BUILDER_VERSION = "cccbdb-experimental-payload-builder/0.1.0"

_DEFAULT_USER_AGENT = (
    "tckdb-cccbdb-importer/0.1 "
    "(+https://github.com/TCKDB/TCKDB; "
    "mailto:calvin.p@campus.technion.ac.il) "
    "phase=2b mode=snapshot"
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetcher contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchResult:
    """Result of a single page fetch.

    Either ``text`` is non-``None`` (success) or ``error`` is
    non-``None`` (failure). ``http_status`` may be ``None`` for
    transport errors that never reached an HTTP exchange.
    ``final_url`` is the URL the response was *served from* after
    HTTP redirects; ``None`` when the transport did not surface one
    (e.g. for cache-served responses).
    """

    text: str | None
    http_status: int | None
    error: str | None
    final_url: str | None = None


class Fetcher(Protocol):
    def __call__(self, url: str) -> FetchResult: ...  # pragma: no cover


class HttpFetcher:
    """Polite real-network fetcher used by the CLI by default.

    Configuration mirrors ``backend/docs/specs/cccbdb_importer.md``
    §10 (Crawler constraints): clear User-Agent, low request rate,
    short timeout, at most one conservative retry.
    """

    def __init__(
        self,
        *,
        user_agent: str = _DEFAULT_USER_AGENT,
        timeout_seconds: float = 20.0,
        retries: int = 1,
        retry_sleep_seconds: float = 2.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.retry_sleep_seconds = retry_sleep_seconds

    def __call__(self, url: str) -> FetchResult:
        try:
            import requests  # local import keeps tests dep-free
        except ImportError as exc:  # pragma: no cover
            return FetchResult(None, None, f"requests not available: {exc}")

        headers = {"User-Agent": self.user_agent}
        last_error: str | None = None
        last_status: int | None = None
        for attempt in range(self.retries + 1):
            try:
                response = requests.get(
                    url, headers=headers, timeout=self.timeout_seconds
                )
                last_status = response.status_code
                response.raise_for_status()
                return FetchResult(
                    text=response.text,
                    http_status=response.status_code,
                    error=None,
                    final_url=response.url,
                )
            except Exception as exc:  # noqa: BLE001 - keep retry logic broad
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.retries:
                    time.sleep(self.retry_sleep_seconds)
                    continue
        return FetchResult(None, last_status, last_error)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RecordResult:
    """One target's per-record outcome, written to the manifest.

    Phase 5a additions (species_all_data only; defaults are safe for
    every other page kind):

    * ``final_url``: URL the response was served from after redirects.
    * ``classification`` / ``classification_reason``: classifier
      verdict on the fetched body. Populated for every page kind that
      runs through the classification gate; ``None`` otherwise.
    * ``accepted_as_data``: ``None`` for ungated page kinds,
      ``True`` when the gate passed, ``False`` when the gate
      rejected the response.
    * ``rejected_html_path``: relative path to ``rejected_html/`` if
      the response was rejected and ``--save-rejected-html`` was set.
    * ``resolver_strategy`` / ``resolver_warnings``: machine token
      naming the resolver path that produced this record and any
      resolver-level warnings (gate verdict, missing CAS, etc.).
    """

    species_key: str
    page_kind: str
    source_url: str
    source_record_key: str | None
    retrieved_at: str | None
    http_status: int | None
    content_sha256: str | None
    raw_html_path: str | None
    parsed_json_path: str | None
    payload_json_path: str | None
    parser_warnings: list[str] = field(default_factory=list)
    builder_warnings: list[str] = field(default_factory=list)
    fetch_warnings: list[str] = field(default_factory=list)
    parser_error: str | None = None
    builder_error: str | None = None
    cache_hit: bool = False
    final_url: str | None = None
    classification: str | None = None
    classification_reason: str | None = None
    accepted_as_data: bool | None = None
    rejected_html_path: str | None = None
    resolver_strategy: str | None = None
    resolver_warnings: list[str] = field(default_factory=list)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "species_key": self.species_key,
            "page_kind": self.page_kind,
            "source_url": self.source_url,
            "source_record_key": self.source_record_key,
            "retrieved_at": self.retrieved_at,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "content_sha256": self.content_sha256,
            "raw_html_path": self.raw_html_path,
            "rejected_html_path": self.rejected_html_path,
            "parsed_json_path": self.parsed_json_path,
            "payload_json_path": self.payload_json_path,
            "classification": self.classification,
            "classification_reason": self.classification_reason,
            "accepted_as_data": self.accepted_as_data,
            "resolver_strategy": self.resolver_strategy,
            "resolver_warnings": self.resolver_warnings,
            "parser_warnings": self.parser_warnings,
            "builder_warnings": self.builder_warnings,
            "fetch_warnings": self.fetch_warnings,
            "parser_error": self.parser_error,
            "builder_error": self.builder_error,
            "cache_hit": self.cache_hit,
        }


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def _short_sha(content_sha256: str) -> str:
    return content_sha256[:12]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, data: Any) -> None:
    _atomic_write_text(
        path,
        json.dumps(data, indent=2, sort_keys=True, default=str) + "\n",
    )


def _find_cached_raw_html(
    raw_dir: Path, species_key: str, page_kind: str
) -> tuple[Path, str] | None:
    """Return the most-recent cached raw HTML for a target, if any.

    Cached files are named ``<prefix>_<species_key>_<sha12>.html``
    where ``<prefix>`` is ``experimental`` for species pages and
    ``property`` for cross-species property tables. We pick the
    lexicographically last match (sha order, deterministic) — there
    should normally be exactly one per target per CCCBDB release.
    """

    if page_kind == "experimental_property_table":
        prefix = "property"
    elif page_kind == "molecule_catalog_inchi_index":
        prefix = "catalog"
    elif page_kind == "species_all_data":
        prefix = "species_alldata"
    else:
        prefix = "experimental"
    candidates = sorted(raw_dir.glob(f"{prefix}_{species_key}_*.html"))
    if not candidates:
        return None
    path = candidates[-1]
    text = path.read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return path, sha


@dataclass
class SnapshotConfig:
    """Caller-tunable snapshot behavior.

    ``dry_run`` suppresses *both* file writes and network fetches. When
    set, the runner serves every target from the cache if available
    and records a ``fetch_warnings`` entry for any target that is not
    already cached. This makes ``--dry-run`` safe to invoke against an
    upstream that rate-limits aggressively (CCCBDB's Cloudflare layer
    will issue 1015 even for a single unrecognized URL pattern).
    """

    output_dir: Path
    fetcher: Fetcher
    write_payloads: bool = False
    force_refresh: bool = False
    sleep_seconds: float = 2.0
    dry_run: bool = False
    max_pages: int | None = None
    strict: bool = False
    save_rejected_html: bool = False


def run_snapshot(
    targets: tuple[CrawlTarget, ...],
    config: SnapshotConfig,
) -> dict[str, Any]:
    """Run the snapshot over ``targets`` and write ``manifest.json``.

    :param targets: Allowlisted pages to snapshot.
    :param config: See :class:`SnapshotConfig`.
    :returns: The manifest dictionary that was written (also returned
        when ``dry_run=True``, in which case no files are written).
    """

    archive_root = config.output_dir
    raw_dir = archive_root / "raw_html"
    parsed_dir = archive_root / "parsed"
    payload_dir = archive_root / "payloads"

    if config.max_pages is not None:
        targets = targets[: config.max_pages]

    rejected_dir = archive_root / "rejected_html"

    if not config.dry_run:
        archive_root.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)
        parsed_dir.mkdir(parents=True, exist_ok=True)
        if config.write_payloads:
            payload_dir.mkdir(parents=True, exist_ok=True)
        if config.save_rejected_html:
            rejected_dir.mkdir(parents=True, exist_ok=True)

    records: list[RecordResult] = []
    for i, target in enumerate(targets):
        if i > 0 and config.sleep_seconds > 0 and not config.dry_run:
            time.sleep(config.sleep_seconds)
        records.append(
            _snapshot_one(
                target,
                config=config,
                raw_dir=raw_dir,
                parsed_dir=parsed_dir,
                payload_dir=payload_dir,
                rejected_dir=rejected_dir,
            )
        )

    manifest = {
        "source": SOURCE_NAME,
        "source_release": SOURCE_RELEASE,
        "source_database_doi": SOURCE_DATABASE_DOI,
        "snapshot_version": SNAPSHOT_VERSION,
        "created_at": _now_utc_iso(),
        "parser_version": PARSER_VERSION,
        "builder_version": BUILDER_VERSION,
        "records": [r.to_manifest() for r in records],
    }

    if not config.dry_run:
        _atomic_write_json(archive_root / "manifest.json", manifest)

    if config.strict and any(
        r.parser_error or r.builder_error or (r.content_sha256 is None)
        for r in records
    ):
        raise SnapshotFailed("Strict mode: at least one record had an error")
    # The "all records failed" guard is a real-fetch concern only;
    # a dry-run against a cold cache legitimately produces zero
    # content and is not a failure.
    if (
        not config.dry_run
        and records
        and all(r.content_sha256 is None for r in records)
    ):
        raise SnapshotFailed("All records failed to fetch")

    return manifest


class SnapshotFailed(RuntimeError):
    """Raised when ``run_snapshot`` exits with a hard failure."""


def _snapshot_one(
    target: CrawlTarget,
    *,
    config: SnapshotConfig,
    raw_dir: Path,
    parsed_dir: Path,
    payload_dir: Path,
    rejected_dir: Path,
) -> RecordResult:
    if target.page_kind == "experimental_property_table":
        base_name = f"property_{target.species_key}"
    elif target.page_kind == "molecule_catalog_inchi_index":
        base_name = f"catalog_{target.species_key}"
    elif target.page_kind == "species_all_data":
        base_name = f"species_alldata_{target.species_key}"
    else:
        base_name = f"experimental_{target.species_key}"
    result = RecordResult(
        species_key=target.species_key,
        page_kind=target.page_kind,
        source_url=target.source_url,
        source_record_key=None,
        retrieved_at=None,
        http_status=None,
        content_sha256=None,
        raw_html_path=None,
        parsed_json_path=None,
        payload_json_path=None,
    )

    html, cache_hit = _resolve_html(target, config, raw_dir, result)
    if html is None:
        # _resolve_html populated fetch_warnings / error fields.
        return result

    content_sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
    short = _short_sha(content_sha256)
    raw_path = raw_dir / f"{base_name}_{short}.html"
    rejected_path = rejected_dir / f"{base_name}_{short}.html"
    parsed_path = parsed_dir / f"{base_name}_{short}.json"
    payload_path = (
        payload_dir / f"{base_name}_{short}.json"
        if config.write_payloads
        else None
    )

    result.content_sha256 = content_sha256
    result.cache_hit = cache_hit

    # ----- Classification gate (species_all_data only) -----------------
    # The direct-CAS resolver path (Phase 5a) has known silent-failure
    # modes — most importantly, ``alldata2x.asp?casno=...`` sometimes
    # 302s to the formula-entry form, returning HTTP 200 with HTML
    # that *looks* fine until you read it. We refuse to save such
    # responses as raw_html, because doing so would let a future
    # parser run treat them as real per-species data.
    if target.page_kind == "species_all_data":
        result.resolver_strategy = "direct_alldata2x_casno"
        verdict = classify_html(
            html,
            attempted_url=target.source_url,
            final_url=result.final_url,
        )
        result.classification = verdict.classification.value
        result.classification_reason = verdict.reason
        if verdict.classification != Classification.molecule_data_page:
            result.accepted_as_data = False
            result.resolver_warnings.append(
                f"rejected by classification gate: "
                f"{verdict.classification.value} ({verdict.reason})"
            )
            if config.save_rejected_html and not config.dry_run and not cache_hit:
                _atomic_write_text(rejected_path, html)
                result.rejected_html_path = str(
                    rejected_path.relative_to(config.output_dir)
                )
            # Rejected pages do NOT land in raw_html/ and are not
            # parsed. Skip the rest of the pipeline.
            return result
        result.accepted_as_data = True

    if not config.dry_run and not cache_hit:
        _atomic_write_text(raw_path, html)

    result.raw_html_path = str(raw_path.relative_to(config.output_dir))

    # --- Parse ----------------------------------------------------------
    try:
        if target.page_kind == "experimental_property_table":
            if target.property_kind is None:
                raise ValueError(
                    "property_kind is required for "
                    "experimental_property_table targets"
                )
            record = parse_experimental_property_table_page(
                html,
                property_kind=target.property_kind,
                source_url=target.source_url,
                source_record_key=target.species_key,
            )
        elif target.page_kind == "molecule_catalog_inchi_index":
            record = parse_molecule_catalog_page(
                html,
                source_url=target.source_url,
                source_record_key=target.species_key,
            )
        elif target.page_kind == "species_all_data":
            record = parse_species_all_data_page(
                html,
                source_url=target.source_url,
                cas_number=target.cas_number,
                source_record_key=target.species_key,
            )
        else:
            record = parse_experimental_species_page(
                html,
                source_url=target.source_url,
                source_record_key=target.species_key,
            )
    except Exception as exc:  # noqa: BLE001 - we want to log + continue
        result.parser_error = f"{type(exc).__name__}: {exc}"
        _logger.warning(
            "Parser failed for %s: %s", target.species_key, result.parser_error
        )
        return result

    result.source_record_key = record.source_metadata.source_record_key
    result.parser_warnings = list(record.warnings)

    parsed_dump = record.model_dump(mode="json")
    if not config.dry_run:
        _atomic_write_json(parsed_path, parsed_dump)
    result.parsed_json_path = str(parsed_path.relative_to(config.output_dir))

    # --- Build (optional) ----------------------------------------------
    # Builders are species-page-specific. Cross-species property
    # tables stop at parsed JSON for now — molecular_property_observation
    # is still a schema gap (see docs/specs/cccbdb_importer.md §7 Gap 1).
    # Molecule-catalog snapshots are identity universe only and never
    # produce TCKDB upload payloads.
    if (
        config.write_payloads
        and payload_path is not None
        and target.page_kind == "experimental_property_table"
    ):
        result.builder_warnings.append(
            "experimental_property_table payloads are deferred until "
            "molecular_property_observation lands"
        )
    elif (
        config.write_payloads
        and payload_path is not None
        and target.page_kind == "molecule_catalog_inchi_index"
    ):
        result.builder_warnings.append(
            "molecule_catalog_inchi_index is identity-universe-only; "
            "catalog entries are not TCKDB upload payloads"
        )
    elif config.write_payloads and payload_path is not None:
        try:
            from app.importers.cccbdb.builders import (
                build_experimental_species_payload,
            )

            build_result = build_experimental_species_payload(record)
        except Exception as exc:  # noqa: BLE001
            result.builder_error = f"{type(exc).__name__}: {exc}"
            _logger.warning(
                "Builder failed for %s: %s",
                target.species_key,
                result.builder_error,
            )
            return result

        result.builder_warnings = list(build_result.warnings)
        payload_dump = build_result.model_dump(mode="json")
        if not config.dry_run:
            _atomic_write_json(payload_path, payload_dump)
        result.payload_json_path = str(
            payload_path.relative_to(config.output_dir)
        )

    return result


def _resolve_html(
    target: CrawlTarget,
    config: SnapshotConfig,
    raw_dir: Path,
    result: RecordResult,
) -> tuple[str | None, bool]:
    """Decide whether to use cached raw HTML or fetch fresh.

    Returns ``(html_or_None, cache_hit)``. On failure populates the
    ``fetch_warnings`` / ``parser_error`` / status fields of ``result``
    and returns ``(None, False)``.
    """

    if not config.force_refresh:
        cached = _find_cached_raw_html(
            raw_dir, target.species_key, target.page_kind
        )
        if cached is not None:
            cached_path, sha = cached
            result.retrieved_at = None  # cache-served: no fresh retrieval
            result.http_status = None
            return cached_path.read_text(encoding="utf-8"), True

    if config.dry_run:
        # Dry-run is fully offline: we already missed the cache above,
        # so the only honest outcome is to record a fetch warning and
        # move on. We deliberately do NOT call the live fetcher here —
        # a single rejected URL is enough to trip the upstream rate
        # limiter, so making dry-run touch the network would defeat
        # the safety story.
        result.fetch_warnings.append(
            "dry-run: no cached raw HTML and network is suppressed"
        )
        return None, False

    fetch = config.fetcher(target.source_url)
    result.retrieved_at = _now_utc_iso()
    result.http_status = fetch.http_status
    result.final_url = fetch.final_url
    if fetch.text is None:
        result.fetch_warnings.append(fetch.error or "unknown fetch error")
        result.parser_error = None  # we did not even reach the parser
        return None, False
    return fetch.text, False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_arg_parser():  # pragma: no cover - thin argparse wrapping
    import argparse

    p = argparse.ArgumentParser(
        prog="cccbdb_snapshot",
        description=(
            "Fetch and archive CCCBDB pages into a deterministic snapshot. "
            "Default pilot: experimental species (H2, H2O, benzene)."
        ),
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/external/cccbdb"),
        help="Archive root directory (default: data/external/cccbdb)",
    )
    p.add_argument(
        "--pilot",
        choices=sorted(PILOTS.keys()),
        default="experimental",
        help="Which allowlisted pilot to snapshot.",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-refresh", action="store_true")
    p.add_argument("--write-payloads", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument(
        "--save-rejected-html",
        action="store_true",
        help=(
            "For ``species_all_data`` targets whose response fails "
            "the classifier gate (redirect to formula-entry, "
            "rate-limit page, etc.), save the rejected HTML to "
            "rejected_html/ for forensic inspection. Without this "
            "flag, rejected bodies are dropped — only the manifest "
            "records the verdict."
        ),
    )
    p.add_argument(
        "--allow-unverified-urls",
        action="store_true",
        help=(
            "Override the CCCBDB URL guardrail. Without this flag the CLI "
            "refuses to fetch any CrawlTarget whose is_validated_url is "
            "False. Current Phase 2b allowlist URLs are all unverified — "
            "see crawl_plan.py for context."
        ),
    )
    p.add_argument("--max-pages", type=int, default=None)
    p.add_argument("--sleep-seconds", type=float, default=2.0)
    p.add_argument("--timeout-seconds", type=float, default=20.0)
    p.add_argument("--user-agent", default=_DEFAULT_USER_AGENT)
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""

    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    targets = PILOTS[args.pilot]

    # Guard the live network path. ``--dry-run`` is fully offline by
    # design (see SnapshotConfig docstring), so it does not need the
    # URL guard — but we keep the check unconditional for an honest
    # error message when someone removes ``--dry-run`` later.
    if not args.allow_unverified_urls and not args.dry_run:
        try:
            assert_all_validated(targets)
        except UnverifiedUrlError as exc:
            _logger.error("%s", exc)
            return 2

    fetcher = HttpFetcher(
        user_agent=args.user_agent,
        timeout_seconds=args.timeout_seconds,
    )
    config = SnapshotConfig(
        output_dir=args.output_dir,
        fetcher=fetcher,
        write_payloads=args.write_payloads,
        force_refresh=args.force_refresh,
        sleep_seconds=args.sleep_seconds,
        dry_run=args.dry_run,
        max_pages=args.max_pages,
        strict=args.strict,
        save_rejected_html=args.save_rejected_html,
    )

    try:
        manifest = run_snapshot(targets, config)
    except SnapshotFailed as exc:
        _logger.error("%s", exc)
        return 1

    n_ok = sum(
        1
        for r in manifest["records"]
        if r["content_sha256"] is not None and not r["parser_error"]
    )
    _logger.info(
        "Snapshot done: %d/%d records ok (output_dir=%s, dry_run=%s)",
        n_ok,
        len(manifest["records"]),
        args.output_dir,
        args.dry_run,
    )
    return 0


__all__ = [
    "BUILDER_VERSION",
    "Fetcher",
    "FetchResult",
    "HttpFetcher",
    "RecordResult",
    "SnapshotConfig",
    "SnapshotFailed",
    "SNAPSHOT_VERSION",
    "main",
    "run_snapshot",
]
