#!/usr/bin/env bash
# Test-profiling helper — surface the slowest tests in a given subset.
#
# Wraps ``pytest --durations=50`` so the top 50 slowest tests are
# printed at the end of the run (regardless of pass/fail). Defaults
# to the whole backend suite when no path is supplied; pass a path
# argument to narrow the scope when the full suite is too slow to
# iterate on.
#
# Examples:
#   bash backend/scripts/test-profile.sh
#   bash backend/scripts/test-profile.sh tests/api/scientific/
#   bash backend/scripts/test-profile.sh tests/api/ -k upload
#   conda run -n tckdb_env bash backend/scripts/test-profile.sh tests/services/
#
# Deliberately serial (workers default to 0): durations measured while eight
# workers contend for one PostgreSQL server describe the contention, not the
# tests. Pass ``TCKDB_TEST_WORKERS=8`` if you want to profile the parallel run
# itself.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=lib/pytest_run_args.sh
source "$(dirname "$0")/lib/pytest_run_args.sh"
TCKDB_DEFAULT_WORKERS=0 tckdb_pytest_run_args "$@"

# Use ``tests/`` as the default target when the caller passed no path.
# Pytest treats ``--durations`` as a top-level option, so additional
# flags can ride along on the same command line.
if [[ $# -eq 0 ]]; then
    exec pytest -v "${TCKDB_PYTEST_ARGS[@]}" tests/ --durations=50
fi

exec pytest -v "${TCKDB_PYTEST_ARGS[@]}" --durations=50 "$@"
