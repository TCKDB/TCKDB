"""CLI entry point for the offline replay engine.

``tckdb-replay <bundle_dir>`` walks a TCKDB Offline Payload Bundle and
posts each pending/failed sidecar via the existing HTTP client. The
CLI is a thin wrapper: it builds a ``client_factory`` closure capturing
``api_key`` and ``timeout``, then hands the bundle to
:func:`tckdb_client.replay.replay_bundle`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from tckdb_client.client import TCKDBClient
from tckdb_client.replay import (
    SUPPORTED_PAYLOAD_KINDS,
    ReplayFailure,
    ReplaySummary,
    replay_bundle,
)

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_ARGPARSE = 2
EXIT_BUNDLE_DIR = 3

DEFAULT_FAILURE_GROUPS_SHOWN = 10


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tckdb-replay",
        description=(
            "Replay a TCKDB Offline Payload Bundle to a TCKDB instance. "
            f"Supported payload_kinds: {', '.join(SUPPORTED_PAYLOAD_KINDS)}."
        ),
    )
    parser.add_argument(
        "bundle_dir",
        help="Path to the bundle directory (e.g. tckdb_payloads/).",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the base_url recorded in sidecars.",
    )
    parser.add_argument(
        "--api-key-env",
        default="TCKDB_API_KEY",
        help="Environment variable holding the API key. Default: TCKDB_API_KEY.",
    )
    parser.add_argument(
        "--only-pending",
        action="store_true",
        help="Skip sidecars whose status is 'failed' (only attempt 'pending').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Walk the bundle without making HTTP calls or mutating sidecars.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-request timeout in seconds. Default: 30.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    parser.add_argument(
        "--show-failures",
        action="store_true",
        help=(
            "List every distinct failure group instead of capping at "
            f"the top {DEFAULT_FAILURE_GROUPS_SHOWN}."
        ),
    )
    return parser


def _format_summary(
    summary: ReplaySummary,
    *,
    show_all_failures: bool = False,
    max_failure_groups: int = DEFAULT_FAILURE_GROUPS_SHOWN,
) -> str:
    lines = [
        "tckdb-replay summary:",
        f"  total                              : {summary.total}",
        f"  uploaded                           : {summary.uploaded}",
        f"  skipped (already uploaded)         : {summary.skipped_already_uploaded}",
        f"  skipped (marked skipped)           : {summary.skipped_marked_skipped}",
        f"  skipped (failed; --only-pending set): "
        f"{summary.skipped_failed_due_to_only_pending}",
        f"  failed                             : {summary.failed}",
        f"  dry_run                            : {summary.dry_run}",
    ]
    if summary.by_kind:
        lines.append("  by kind:")
        for kind in sorted(summary.by_kind):
            buckets = summary.by_kind[kind]
            parts = ", ".join(f"{k}={v}" for k, v in sorted(buckets.items()))
            lines.append(f"    {kind}: {parts}")

    if summary.failures:
        lines.extend(
            _format_failure_groups(
                summary.failures,
                show_all=show_all_failures,
                max_groups=max_failure_groups,
            )
        )

    return "\n".join(lines)


def _group_failures(
    failures: tuple[ReplayFailure, ...],
) -> list[tuple[str, str, int, str]]:
    """Group failures by (payload_kind, last_error).

    Returns a list of ``(kind, last_error, count, sample_path)`` tuples
    sorted by count descending. Identical errors (e.g. 72 sidecars all
    missing ``payload_kind``) collapse to one row, so the operator sees
    *what* went wrong at a glance instead of 72 near-identical lines.
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    for f in failures:
        grouped.setdefault((f.payload_kind, f.last_error), []).append(
            f.sidecar_path
        )
    rows = [
        (kind, err, len(paths), paths[0])
        for (kind, err), paths in grouped.items()
    ]
    rows.sort(key=lambda r: (-r[2], r[0], r[1]))
    return rows


def _format_failure_groups(
    failures: tuple[ReplayFailure, ...],
    *,
    show_all: bool,
    max_groups: int,
) -> list[str]:
    rows = _group_failures(failures)
    total = sum(r[2] for r in rows)
    lines = [
        "",
        f"Failure breakdown ({total} sidecar{'s' if total != 1 else ''}, "
        f"{len(rows)} distinct error{'s' if len(rows) != 1 else ''}):",
    ]
    shown = rows if show_all else rows[:max_groups]
    width = max(len(str(r[2])) for r in shown)
    for kind, err, count, sample in shown:
        lines.append(f"  {count:>{width}}× [{kind}] {err}")
        lines.append(f"  {' ' * width}  e.g. {sample}")
    hidden = len(rows) - len(shown)
    if hidden > 0:
        hidden_count = sum(r[2] for r in rows[len(shown):])
        lines.append(
            f"  … and {hidden} more distinct error"
            f"{'s' if hidden != 1 else ''} "
            f"covering {hidden_count} sidecar"
            f"{'s' if hidden_count != 1 else ''} "
            f"(use --show-failures to list them all)"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    bundle_dir = Path(args.bundle_dir)
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        print(
            f"error: bundle_dir does not exist or is not a directory: {bundle_dir}",
            file=sys.stderr,
        )
        return EXIT_BUNDLE_DIR

    api_key = os.environ.get(args.api_key_env)
    if not args.dry_run and not api_key:
        parser.error(
            f"API key env var {args.api_key_env!r} is not set "
            "(required unless --dry-run is given)"
        )

    timeout = args.timeout

    def _client_factory(base_url: str) -> TCKDBClient:
        return TCKDBClient(base_url=base_url, api_key=api_key, timeout=timeout)

    summary = replay_bundle(
        bundle_dir,
        client_factory=_client_factory,
        base_url_override=args.base_url,
        only_pending=args.only_pending,
        dry_run=args.dry_run,
    )

    print(
        _format_summary(
            summary,
            show_all_failures=args.show_failures,
            max_failure_groups=DEFAULT_FAILURE_GROUPS_SHOWN,
        )
    )
    return EXIT_OK if summary.failed == 0 else EXIT_FAILURES


if __name__ == "__main__":
    sys.exit(main())
