#!/usr/bin/env python
"""Regenerate ``docs/api_parity_matrix.md`` from the coverage ledger.

The matrix is derived, never hand-edited: its single source of truth is
``tckdb_client._parity.COVERAGE``. ``tests/test_parity_matrix_doc.py``
re-runs :func:`render_matrix` and compares, so a stale checked-in doc
fails the suite.

Usage
-----
    python scripts/generate_parity_matrix.py           # rewrite the doc
    python scripts/generate_parity_matrix.py --check   # exit 1 if stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT / "src"))

from tckdb_client._parity import (  # noqa: E402  (path bootstrap must come first)
    COVERAGE,
    OperationCoverage,
    coverage_counts,
    entries_with_status,
)

DOC_PATH = _PACKAGE_ROOT / "docs" / "api_parity_matrix.md"

_COLUMNS = (
    "Operation",
    "raw HTTP",
    "typed client",
    "iterator",
    "example",
    "contract test",
)
_EMPTY = "—"


def _cell(value: str | None) -> str:
    return f"`{value}`" if value else _EMPTY


def _row(entry: OperationCoverage) -> str:
    cells = (
        f"`{entry.operation}`",
        # Every operation is reachable with request_json/get_json/post_json;
        # the client never hides an endpoint behind a typed method.
        "yes",
        _cell(entry.client_method),
        _cell(entry.iterator),
        _cell(entry.example),
        _cell(entry.contract_test),
    )
    return "| " + " | ".join(cells) + " |"


def _table(entries: tuple[OperationCoverage, ...]) -> list[str]:
    lines = [
        "| " + " | ".join(_COLUMNS) + " |",
        "|" + "|".join(["---"] * len(_COLUMNS)) + "|",
    ]
    lines.extend(_row(entry) for entry in entries)
    return lines


def _grouped_by_reason(
    entries: tuple[OperationCoverage, ...],
) -> list[tuple[str, tuple[OperationCoverage, ...]]]:
    order: list[str] = []
    buckets: dict[str, list[OperationCoverage]] = {}
    for entry in entries:
        reason = entry.reason or ""
        if reason not in buckets:
            buckets[reason] = []
            order.append(reason)
        buckets[reason].append(entry)
    return [(reason, tuple(buckets[reason])) for reason in order]


def render_matrix() -> str:
    """Render the full Markdown document as a string."""

    counts = coverage_counts()
    total = len(COVERAGE)
    lines: list[str] = [
        "# TCKDB Python client — API parity matrix",
        "",
        "<!-- GENERATED FILE — do not edit by hand.",
        "     Source: clients/python/src/tckdb_client/_parity.py",
        "     Regenerate: python scripts/generate_parity_matrix.py",
        "     Enforced by: tests/test_parity_matrix_doc.py -->",
        "",
        "Every operation in the backend's OpenAPI document"
        " (`backend/tests/api/golden/openapi.json`) is triaged here exactly"
        " once. `tests/test_openapi_parity.py` fails when the backend gains"
        " an operation that has not been classified, so this table cannot"
        " silently fall behind the API.",
        "",
        "**Every** operation is reachable over raw HTTP via"
        " `TCKDBClient.request_json()` / `get_json()` / `post_json()` — the"
        " client never hides an endpoint. The `typed client` column records"
        " where a first-class method exists on top of that.",
        "",
        "| Classification | Operations |",
        "|---|---|",
        f"| typed | {counts['typed']} |",
        f"| raw_only | {counts['raw_only']} |",
        f"| not_applicable | {counts['not_applicable']} |",
        f"| **total** | **{total}** |",
        "",
        "## Typed coverage",
        "",
        "A first-class client method exists for these operations.",
        "",
    ]
    lines.extend(_table(entries_with_status("typed")))

    lines.extend(
        [
            "",
            "## Raw HTTP only",
            "",
            "Deliberately reachable only through the generic request"
            " helpers. Each group states why a typed method would not earn"
            " its keep.",
            "",
        ]
    )
    for reason, entries in _grouped_by_reason(entries_with_status("raw_only")):
        lines.append(f"> {reason}")
        lines.append("")
        lines.extend(_table(entries))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()

    lines.extend(
        [
            "",
            "## Not applicable",
            "",
            "Admin, auth, and curator-internal surface that a"
            " producer/consumer client is not meant to drive.",
            "",
        ]
    )
    for reason, entries in _grouped_by_reason(entries_with_status("not_applicable")):
        lines.append(f"> {reason}")
        lines.append("")
        lines.extend(_table(entries))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()

    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the checked-in doc differs from the ledger",
    )
    args = parser.parse_args(argv)

    rendered = render_matrix()
    if args.check:
        if not DOC_PATH.is_file():
            print(f"{DOC_PATH} is missing; run without --check.", file=sys.stderr)
            return 1
        current = DOC_PATH.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"{DOC_PATH} is stale; run "
                "`python scripts/generate_parity_matrix.py`.",
                file=sys.stderr,
            )
            return 1
        print(f"{DOC_PATH} is up to date.")
        return 0

    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {DOC_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
