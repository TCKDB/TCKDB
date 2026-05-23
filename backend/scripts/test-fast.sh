#!/usr/bin/env bash
# Tier 0 / Tier 1: inner-loop test runner.
#
# Wraps ``pytest -q -x --tb=short`` so a single failing test or file
# stops the run immediately with a short traceback. Pass any pytest
# arguments through; the typical use is a path plus a ``-k`` selector.
#
# Examples:
#   bash backend/scripts/test-fast.sh tests/api/test_api_health.py
#   bash backend/scripts/test-fast.sh tests/api/test_request_id.py -k generated
#   conda run -n tckdb_env bash backend/scripts/test-fast.sh tests/api/test_api_health.py
#
# Conda is intentionally NOT invoked here so the script is composable
# with whatever python the caller has on PATH (uv, venv, conda).
set -euo pipefail

cd "$(dirname "$0")/.."
exec pytest -q -x --tb=short "$@"
