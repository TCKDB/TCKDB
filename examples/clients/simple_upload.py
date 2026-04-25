"""Generic authenticated request against a configured TCKDB instance.

This example is intentionally workflow-tool agnostic. It demonstrates the
two-value targeting model used by every TCKDB client:

    TCKDB_BASE_URL   — the API root of the target instance, e.g.
                       http://localhost:8000/api/v1 (local),
                       http://lab-tckdb.internal:8000/api/v1 (lab-server),
                       https://tckdb.example.org/api/v1 (hosted).
    TCKDB_API_KEY    — an API key minted on THAT instance.

Run:
    export TCKDB_BASE_URL="http://localhost:8000/api/v1"
    export TCKDB_API_KEY="tck_replace_me"
    python examples/clients/simple_upload.py
"""

from __future__ import annotations

import os
import sys

import requests


def main() -> int:
    try:
        base_url = os.environ["TCKDB_BASE_URL"].rstrip("/")
        api_key = os.environ["TCKDB_API_KEY"]
    except KeyError as missing:
        print(f"missing required environment variable: {missing.args[0]}", file=sys.stderr)
        return 2

    response = requests.get(
        f"{base_url}/auth/me",
        headers={"X-API-Key": api_key},
        timeout=30,
    )
    response.raise_for_status()
    print(response.json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
