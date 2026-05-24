#!/usr/bin/env bash
# Regenerate the OpenAPI golden snapshot at
# ``backend/tests/api/golden/openapi.json``.
#
# Sets ``UPDATE_OPENAPI_GOLDEN=1`` and runs the snapshot test, which
# (in update mode) rewrites the golden file from the live
# ``/openapi.json`` instead of asserting against it. Review the diff
# before committing:
#
#   git diff backend/tests/api/golden/openapi.json
#
# Examples:
#   bash backend/scripts/update-openapi-golden.sh
#   bash backend/scripts/update-openapi-golden.sh -x --tb=short
#   conda run -n tckdb_env bash backend/scripts/update-openapi-golden.sh
#
# Conda is intentionally NOT invoked here so the script is composable
# with whatever python the caller has on PATH (uv, venv, conda).
set -euo pipefail

cd "$(dirname "$0")/.."

UPDATE_OPENAPI_GOLDEN=1 exec pytest tests/api/test_openapi_snapshot.py "$@"
