#!/usr/bin/env python
"""CLI wrapper for the CCCBDB resolver-diagnostic tool.

Usage:

    conda run -n tckdb_env python -m scripts.cccbdb_resolver_diagnostics \\
        --output-json /tmp/cccbdb_resolver_diagnostics.json \\
        --sleep-seconds 2

Live diagnostics hit cccbdb.nist.gov; do not run this in CI. See
``backend/app/importers/cccbdb/diagnostics/`` for runner internals
and the README for context.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importers.cccbdb.diagnostics.runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
