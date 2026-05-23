#!/usr/bin/env bash
# Tier 3: full API suite.
#
# Runs every test under ``tests/api/`` — the cross-surface regression
# gate for any backend change that touches a route, middleware, or
# request/response shape. Slower than the scientific tier (10+ minutes
# on a cold machine); pair with ``-x`` if you want fail-fast on a
# suspected regression.
#
# Examples:
#   bash backend/scripts/test-api.sh
#   bash backend/scripts/test-api.sh -x
#   conda run -n tckdb_env bash backend/scripts/test-api.sh
set -euo pipefail

cd "$(dirname "$0")/.."
exec pytest -q tests/api/ "$@"
