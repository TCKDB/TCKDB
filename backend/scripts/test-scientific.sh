#!/usr/bin/env bash
# Tier 2/3: scientific read/service confidence run.
#
# Covers the public scientific API surface and the supporting service
# layer. Use this before committing changes to anything under
# ``app/api/routes/scientific/`` or ``app/services/scientific_read/``.
# Extra pytest args (``-k``, ``-x``, ``--maxfail=...``) are forwarded.
#
# Runs with a pinned random-order seed and parallel workers; see
# scripts/lib/pytest_run_args.sh for why, and how to override either.
#
# Examples:
#   bash backend/scripts/test-scientific.sh
#   bash backend/scripts/test-scientific.sh -k species
#   conda run -n tckdb_env bash backend/scripts/test-scientific.sh
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
exec pytest -q --tb=short "${TCKDB_PYTEST_ARGS[@]}" \
    tests/api/scientific/ tests/services/scientific_read/ "$@"
