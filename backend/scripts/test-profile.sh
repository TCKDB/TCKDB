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
set -euo pipefail

cd "$(dirname "$0")/.."

# Use ``tests/`` as the default target when the caller passed no path.
# Pytest treats ``--durations`` as a top-level option, so additional
# flags can ride along on the same command line.
if [[ $# -eq 0 ]]; then
    exec pytest -v tests/ --durations=50
fi

exec pytest -v --durations=50 "$@"
