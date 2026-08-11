#!/usr/bin/env bash
# Tier 3: full API suite.
#
# Runs every test under ``tests/api/`` — the cross-surface regression
# gate for any backend change that touches a route, middleware, or
# request/response shape. Pair with ``-x`` if you want fail-fast on a
# suspected regression.
#
# Excludes: everything outside ``tests/api/``. This is the API gate, not
# the backend gate. The service, workflow, db, schema, parser, importer
# and invariant tests are the complement gate's -- scripts/test-rest.sh --
# and a green run here says nothing about any of them. Saying so is not
# pedantry: for most of this repo's life the name was read as coverage it
# did not have, and half the suite gated no pull request as a result.
#
# CI narrows the selection further, and both narrowings are covered
# elsewhere rather than dropped:
#
#   --ignore=tests/api/scientific/            -> scripts/test-scientific.sh
#   --ignore=tests/api/test_openapi_snapshot.py -> its own workflow step
#
# tests/scripts/test_gate_coverage.py checks that claim against the
# workflow rather than trusting this comment.
#
# Runs with a pinned random-order seed and parallel workers; see
# scripts/lib/pytest_run_args.sh for why, and how to override either.
#
# Examples:
#   bash backend/scripts/test-api.sh
#   bash backend/scripts/test-api.sh -x
#   conda run -n tckdb_env bash backend/scripts/test-api.sh
set -euo pipefail

# Resolve the script directory *before* changing directory. "$0" is relative
# to the invoking cwd, so re-deriving it after the cd resolves against the
# new one -- which works from backend/ and fails from the repo root, the way
# CI invokes these.
TCKDB_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TCKDB_SCRIPT_DIR/.."
# shellcheck source=lib/pytest_run_args.sh
source "$TCKDB_SCRIPT_DIR/lib/pytest_run_args.sh"
tckdb_pytest_run_args "$@"
exec pytest -q --tb=short "${TCKDB_PYTEST_ARGS[@]}" tests/api/ "$@"
