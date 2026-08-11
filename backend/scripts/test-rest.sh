#!/usr/bin/env bash
# Tier 3: the complement gate -- every test the other two PR gates do not run.
#
# Selects ``tests/`` and subtracts exactly the two subtrees the other required
# gates already own:
#
#   tests/api/                      -> scripts/test-api.sh
#   tests/services/scientific_read/ -> scripts/test-scientific.sh
#
# Excludes: nothing else, ever. That is the whole point of the script, and it
# is why the selection is written as a subtraction rather than as a list of
# directories to include.
#
# ---------------------------------------------------------------------------
# What this gate exists to stop happening again
# ---------------------------------------------------------------------------
# Until it existed, the two required PR jobs ran ``tests/api/`` and
# ``tests/api/scientific/`` + ``tests/services/scientific_read/`` and nothing
# else. Everything under tests/db/, tests/workflows/, tests/invariants/,
# tests/integration/, tests/workers/, tests/schemas/, tests/importers/,
# tests/parsers/, tests/cli/, tests/smoke/, tests/examples/,
# tests/client_builder_contract/ and tests/services/ outside scientific_read --
# 3,806 tests, more than half the suite by count -- ran on no pull request at
# all. It ran nightly, which meant a defect merged green and surfaced the next
# morning attached to no PR. ``tests/db/test_identifier_lengths.py`` sat red on
# main for days that way, and two branches merged whose failures both gates
# reported green.
#
# An enumerated include-list would have reproduced that defect the first time
# somebody added a directory under tests/ and did not think to add it here. A
# subtraction cannot: a new directory joins a required gate the day it is
# created. ``tests/scripts/test_gate_coverage.py`` pins the subtraction against
# the scripts it subtracts and against the workflow that runs them, so the
# three selections keep covering tests/ rather than drifting back into a
# gap nobody can see.
#
# ---------------------------------------------------------------------------
# Wall clock
# ---------------------------------------------------------------------------
# These are the cheap tests, not the slow ones: 3,813 of them in 3m50s at four
# workers, against 1,998 scientific tests in 4m21s on the same host. More than
# half the suite by count, and it costs less than the gate that was already
# there -- which is why gating it was never the trade-off it looked like.
#
# On CI the whole job takes 6m36s including container and conda setup, of which
# 5m03s is this run (measured, run 31474580773) -- against the scientific gate's
# 12m09s (run 31431866497). The scientific gate is and remains the critical
# path, so this one runs beside the other two without moving how long a pull
# request waits. It costs a runner, not latency. Folding the same tests into an
# existing gate instead would have put them in series and moved that path out by
# about five minutes on every PR.
#
# Runs with a pinned random-order seed and parallel workers; see
# scripts/lib/pytest_run_args.sh for why, and how to override either.
#
# Examples:
#   bash backend/scripts/test-rest.sh
#   bash backend/scripts/test-rest.sh -x
#   bash backend/scripts/test-rest.sh -k torsion
#   conda run -n tckdb_env bash backend/scripts/test-rest.sh
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
    tests/ --ignore=tests/api --ignore=tests/services/scientific_read "$@"
