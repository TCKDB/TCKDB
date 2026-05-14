"""Upload a pre-built JSON payload to a TCKDB upload endpoint.

The client is generic — it does not construct payloads. This script
just reads bytes from disk and POSTs them. Producing the JSON is the
caller's job (or the job of a producer-specific adapter).

    export TCKDB_BASE_URL="http://localhost:8010/api/v1"
    export TCKDB_API_KEY="tck_replace_me"
    python examples/upload_json_file.py \\
      --endpoint /uploads/conformers \\
      --payload ./payload.json \\
      --idempotency-key "example:upload:conformer:001"
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
    parser.add_argument(
        "--endpoint",
        required=True,
        help="Upload endpoint path or short name (e.g. '/uploads/thermo' or 'thermo').",
    )
    parser.add_argument(
        "--payload",
        type=Path,
        required=True,
        help="Path to a JSON file containing the upload payload.",
    )
    parser.add_argument(
        "--idempotency-key",
        default=None,
        help="Optional Idempotency-Key for retry safety.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        base_url = os.environ["TCKDB_BASE_URL"]
        api_key = os.environ["TCKDB_API_KEY"]
    except KeyError as missing:
        print(f"missing required environment variable: {missing.args[0]}", file=sys.stderr)
        return 2

    if not args.payload.is_file():
        print(f"payload not found: {args.payload}", file=sys.stderr)
        return 2

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    with TCKDBClient(base_url, api_key=api_key) as client:
        try:
            response = client.request_json(
                "POST",
                args.endpoint
                if args.endpoint.startswith(("/", "http://", "https://"))
                else f"/uploads/{args.endpoint}",
                json=payload,
                idempotency_key=args.idempotency_key,
            )
        except TCKDBError as exc:
            print(f"upload failed: {exc}", file=sys.stderr)
            return 1

    print(json.dumps(response.data, indent=2))
    if response.idempotency_replayed:
        print("(server replayed a prior response for this idempotency key)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
