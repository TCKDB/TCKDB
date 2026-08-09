#!/usr/bin/env bash
# Tier 4: full backend test suite (release / pre-push gate).
#
# Runs everything under ``backend/tests/``. Treat this as the
# pre-push and pre-release confidence gate, NOT the edit loop —
# expect a multi-minute runtime. Forwards extra pytest args.
#
# Runs with a pinned random-order seed and parallel workers; see
# scripts/lib/pytest_run_args.sh for why, and how to override either.
#
# Examples:
#   bash backend/scripts/test-full.sh
#   bash backend/scripts/test-full.sh --maxfail=3
#   TCKDB_TEST_SEED=7 bash backend/scripts/test-full.sh     # a different order
#   TCKDB_TEST_WORKERS=0 bash backend/scripts/test-full.sh  # serial
#   conda run -n tckdb_env bash backend/scripts/test-full.sh
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=lib/pytest_run_args.sh
source "$(dirname "$0")/lib/pytest_run_args.sh"
tckdb_pytest_run_args "$@"
exec pytest -q --tb=short "${TCKDB_PYTEST_ARGS[@]}" tests/ "$@"
