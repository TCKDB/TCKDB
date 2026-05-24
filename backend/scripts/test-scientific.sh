#!/usr/bin/env bash
# Tier 2/3: scientific read/service confidence run.
#
# Covers the public scientific API surface and the supporting service
# layer. Use this before committing changes to anything under
# ``app/api/routes/scientific/`` or ``app/services/scientific_read/``.
# Extra pytest args (``-k``, ``-x``, ``--maxfail=...``) are forwarded.
#
# Examples:
#   bash backend/scripts/test-scientific.sh
#   bash backend/scripts/test-scientific.sh -k species
#   conda run -n tckdb_env bash backend/scripts/test-scientific.sh
set -euo pipefail

cd "$(dirname "$0")/.."
exec pytest -q --tb=short tests/api/scientific/ tests/services/scientific_read/ "$@"
