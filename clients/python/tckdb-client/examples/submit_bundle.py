"""Dry-run or submit a contribution bundle to TCKDB.

    export TCKDB_BASE_URL="https://tckdb.example.org/api/v1"
    export TCKDB_API_KEY="tck_replace_me"
    python examples/submit_bundle.py --dry-run ./bundle.json
    python examples/submit_bundle.py --submit  ./bundle.json \\
        --idempotency-key "example:bundle:thermo:run-001"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from tckdb_client import TCKDBClient, TCKDBError


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path, help="Path to a v0 contribution bundle JSON file.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run")
    mode.add_argument("--submit", dest="mode", action="store_const", const="submit")
    parser.add_argument("--idempotency-key", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        base_url = os.environ["TCKDB_BASE_URL"]
        api_key = os.environ["TCKDB_API_KEY"]
    except KeyError as missing:
        print(f"missing required environment variable: {missing.args[0]}", file=sys.stderr)
        return 2

    if not args.bundle.is_file():
        print(f"bundle not found: {args.bundle}", file=sys.stderr)
        return 2

    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))

    with TCKDBClient(base_url, api_key=api_key, timeout=60.0) as client:
        try:
            if args.mode == "dry-run":
                result = client.bundle_dry_run(bundle)
            else:
                result = client.bundle_submit(
                    bundle, idempotency_key=args.idempotency_key
                )
        except TCKDBError as exc:
            print(f"bundle {args.mode} failed: {exc}", file=sys.stderr)
            return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
