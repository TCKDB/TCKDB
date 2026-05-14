"""Basic usage of the TCKDB client.

Reads ``TCKDB_BASE_URL`` and ``TCKDB_API_KEY`` from the environment and
prints the health response and the authenticated user profile.

    export TCKDB_BASE_URL="http://localhost:8010/api/v1"
    export TCKDB_API_KEY="tck_replace_me"
    python examples/basic_usage.py
"""

from __future__ import annotations

import json
import os
import sys

from tckdb_client import TCKDBClient, TCKDBError


def main() -> int:
    try:
        base_url = os.environ["TCKDB_BASE_URL"]
        api_key = os.environ["TCKDB_API_KEY"]
    except KeyError as missing:
        print(f"missing required environment variable: {missing.args[0]}", file=sys.stderr)
        return 2

    with TCKDBClient(base_url, api_key=api_key) as client:
        try:
            print("health:", json.dumps(client.health()))
            print("me:", json.dumps(client.me()))
        except TCKDBError as exc:
            print(f"TCKDB request failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
