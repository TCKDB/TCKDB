"""CLI entry point for the CCCBDB form-page resolver.

Reads a JSON queue file describing which (formula, target_kind)
records to fetch, drives the session-aware POST resolver against
the CCCBDB form endpoints, and writes raw HTML + parsed JSON +
manifest entries under ``--output-dir``.

Usage::

    conda run -n tckdb_env python -m scripts.cccbdb_resolve_form_page \\
      --queue-file data/external/cccbdb/form_queue.json \\
      --output-dir data/external/cccbdb \\
      --max-pages 3 \\
      --sleep-seconds 15 \\
      --save-rejected-html

Exit codes:
    0 — queue resolved (some records may have been rejected; check
        per-record ``classification`` in manifest.json).
    2 — configuration / queue parse error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.importers.cccbdb.form_resolver import (
    DEFAULT_USER_AGENT,
    FormResolverConfig,
    SelectionPolicy,
    load_queue_file,
    run_form_resolver_queue,
)

# CLI-friendly aliases that swap underscores for dashes. The user
# can type ``--selection-policy exact-match`` and the resolver still
# gets the canonical enum value.
_SELECTION_POLICY_ALIASES: dict[str, SelectionPolicy] = {
    "reject-ambiguous": SelectionPolicy.REJECT_AMBIGUOUS,
    "reject_ambiguous": SelectionPolicy.REJECT_AMBIGUOUS,
    "exact-match": SelectionPolicy.EXACT_MATCH,
    "exact_match": SelectionPolicy.EXACT_MATCH,
}

_logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cccbdb_resolve_form_page",
        description=(
            "Resolve CCCBDB form-only experimental pages via "
            "session-aware POST. Conservative — accepts only "
            "unambiguous single-species results today."
        ),
    )
    p.add_argument(
        "--queue-file", type=Path, required=True,
        help="JSON file listing the records to resolve.",
    )
    p.add_argument(
        "--output-dir", type=Path, required=True,
        help="Archive root (raw_html/, parsed/, manifest.json live here).",
    )
    p.add_argument(
        "--max-pages", type=int, default=3,
        help="Maximum number of records to attempt (default: 3).",
    )
    p.add_argument(
        "--sleep-seconds", type=float, default=15.0,
        help="Polite delay between successive POSTs (default: 15s).",
    )
    p.add_argument(
        "--save-rejected-html", action="store_true",
        help="Archive rejected pages under rejected_html/.",
    )
    p.add_argument(
        "--allow-unknown", action="store_true",
        help=(
            "Accept pages whose classification is ``unknown`` instead of "
            "rejecting. Use only for diagnostic runs."
        ),
    )
    p.add_argument(
        "--user-agent", default=DEFAULT_USER_AGENT,
        help="Override the HTTP User-Agent string.",
    )
    p.add_argument(
        "--selection-policy",
        default="reject-ambiguous",
        help=(
            "How to handle CCCBDB ``choosex.asp`` species-selection "
            "pages. ``reject-ambiguous`` (default) rejects every "
            "selection page outright. ``exact-match`` parses the "
            "candidates and selects exactly one only if the queue "
            "record matches it unambiguously on formula+name, "
            "formula+CAS, or formula+InChIKey."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.queue_file.exists():
        _logger.error("queue file does not exist: %s", args.queue_file)
        return 2
    try:
        records = load_queue_file(args.queue_file)
    except (ValueError, OSError) as exc:
        _logger.error("failed to load queue file: %s", exc)
        return 2

    if not records:
        _logger.error("queue file has no records")
        return 2

    policy_key = args.selection_policy.lower().strip()
    selection_policy = _SELECTION_POLICY_ALIASES.get(policy_key)
    if selection_policy is None:
        _logger.error(
            "unknown --selection-policy %r; expected one of: %s",
            args.selection_policy,
            ", ".join(sorted({k for k in _SELECTION_POLICY_ALIASES if "-" in k})),
        )
        return 2

    config = FormResolverConfig(
        output_dir=args.output_dir,
        sleep_seconds=args.sleep_seconds,
        max_pages=args.max_pages,
        save_rejected_html=args.save_rejected_html,
        allow_unknown=args.allow_unknown,
        user_agent=args.user_agent,
        selection_policy=selection_policy,
    )

    summary = run_form_resolver_queue(records, config)
    _logger.info(
        "Form resolver done: %d seen, %d accepted, %d rejected%s",
        summary.records_seen,
        summary.accepted,
        summary.rejected,
        " (stopped after rate-limit)"
        if summary.stopped_after_rate_limit else "",
    )
    for r in summary.results:
        _logger.info(
            "  %s [%s] -> %s (raw=%s parsed=%s)",
            r.species_key,
            r.target_kind,
            r.classification,
            r.raw_html_path or "-",
            r.parsed_json_path or "-",
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
