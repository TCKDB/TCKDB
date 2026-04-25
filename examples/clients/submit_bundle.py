"""Minimal helper for the manual local-to-hosted contribution flow.

Reads ``TCKDB_BASE_URL`` and ``TCKDB_API_KEY`` from the environment,
accepts a bundle path, and supports ``--dry-run`` or ``--submit`` modes.
This is functionally equivalent to the ``curl`` examples in
``docs/contribution-bundles/manual-local-to-hosted-v0.md`` — it contains
no business logic, only HTTP wiring.

Usage::

    export TCKDB_BASE_URL="https://tckdb.example.org/api/v1"
    export TCKDB_API_KEY="tck_hosted_replace_me"

    python examples/clients/submit_bundle.py --dry-run ./thermo-bundle.tckdb.json
    python examples/clients/submit_bundle.py --submit  ./thermo-bundle.tckdb.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "bundle",
        type=Path,
        help="Path to a v0 contribution bundle JSON file.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        dest="mode",
        action="store_const",
        const="dry-run",
        help="Preview against the hosted instance without writing.",
    )
    mode.add_argument(
        "--submit",
        dest="mode",
        action="store_const",
        const="submit",
        help="Submit the bundle to the hosted instance (writes records).",
    )
    return parser.parse_args(argv)


def _load_env() -> tuple[str, str]:
    try:
        base_url = os.environ["TCKDB_BASE_URL"].rstrip("/")
        api_key = os.environ["TCKDB_API_KEY"]
    except KeyError as missing:
        print(
            f"missing required environment variable: {missing.args[0]}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return base_url, api_key


def _post_bundle(base_url: str, api_key: str, mode: str, bundle: Path) -> requests.Response:
    endpoint = "/bundles/dry-run" if mode == "dry-run" else "/bundles/submit"
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    return requests.post(
        f"{base_url}{endpoint}",
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )


def _handle_response(mode: str, response: requests.Response) -> int:
    """Print a compact response summary and return a shell-friendly exit code.

    Pipeable: dry-run exits non-zero on a blocking preview so
    ``--dry-run && --submit`` short-circuits the way you'd expect.
    """
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text}

    if response.status_code >= 400:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    summary = payload.get("summary", {})
    print(f"{mode}: HTTP {response.status_code}")

    if mode == "dry-run":
        print(
            "summary: "
            f"errors={summary.get('errors', 0)}, "
            f"unsupported={summary.get('unsupported', 0)}, "
            f"warnings={summary.get('warnings', 0)}, "
            f"would_create={summary.get('would_create', 0)}, "
            f"would_reuse={summary.get('would_reuse', 0)}, "
            f"would_append={summary.get('would_append', 0)}"
        )
        for item in payload.get("items", [])[:10]:
            print(f"- {item.get('record_type')}: {item.get('action')} — {item.get('reason')}")
        return int(
            not payload.get("bundle_valid", False)
            or summary.get("errors", 0) > 0
            or summary.get("unsupported", 0) > 0
        )

    print(
        "summary: "
        f"submission_id={payload.get('submission_id')}, "
        f"status={payload.get('status')}, "
        f"review_status={payload.get('review_status')}, "
        f"records_imported={summary.get('records_imported', 0)}, "
        f"records_linked={summary.get('records_linked', 0)}, "
        f"warnings={summary.get('warnings', 0)}"
    )
    print("Imported records are visible but unreviewed until curator review.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.bundle.is_file():
        print(f"bundle not found: {args.bundle}", file=sys.stderr)
        return 2
    base_url, api_key = _load_env()
    response = _post_bundle(base_url, api_key, args.mode, args.bundle)
    return _handle_response(args.mode, response)


if __name__ == "__main__":
    raise SystemExit(main())
