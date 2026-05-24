#!/usr/bin/env bash
# Tier 4: full backend test suite (release / pre-push gate).
#
# Runs everything under ``backend/tests/``. Treat this as the
# pre-push and pre-release confidence gate, NOT the edit loop —
# expect a multi-minute runtime. Forwards extra pytest args.
#
# Examples:
#   bash backend/scripts/test-full.sh
#   bash backend/scripts/test-full.sh --maxfail=3
#   conda run -n tckdb_env bash backend/scripts/test-full.sh
set -euo pipefail

cd "$(dirname "$0")/.."
exec pytest -q --tb=short tests/ "$@"
