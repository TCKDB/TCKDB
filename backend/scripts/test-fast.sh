#!/usr/bin/env bash
# Tier 0 / Tier 1: inner-loop test runner.
#
# Wraps ``pytest -v -x --tb=short`` so each test name prints as it
# runs and a single failing test or file stops the run immediately
# with a short traceback. Pass any pytest arguments through; the
# typical use is a path plus a ``-k`` selector.
#
# Examples:
#   bash backend/scripts/test-fast.sh tests/api/test_api_health.py
#   bash backend/scripts/test-fast.sh tests/api/test_request_id.py -k generated
#   conda run -n tckdb_env bash backend/scripts/test-fast.sh tests/api/test_api_health.py
#
# Conda is intentionally NOT invoked here so the script is composable
# with whatever python the caller has on PATH (uv, venv, conda).
#
# The random-order seed is pinned (see scripts/lib/pytest_run_args.sh) so a
# failure you are chasing reproduces on the next invocation. Workers default to
# 0 here: each xdist worker creates and migrates its own database, which is pure
# overhead when the target is one file.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=lib/pytest_run_args.sh
source "$(dirname "$0")/lib/pytest_run_args.sh"
TCKDB_DEFAULT_WORKERS=0 tckdb_pytest_run_args "$@"
exec pytest -v -x --tb=short "${TCKDB_PYTEST_ARGS[@]}" "$@"
