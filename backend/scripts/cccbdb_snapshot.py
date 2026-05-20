#!/usr/bin/env python
"""CLI wrapper for the CCCBDB snapshot archive command.

Usage:

    conda run -n tckdb_env python -m scripts.cccbdb_snapshot \\
        --output-dir data/external/cccbdb \\
        --pilot experimental

See ``backend/app/importers/cccbdb/snapshot.py`` for the runner and
``backend/app/importers/cccbdb/README.md`` for archive layout and
policy.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importers.cccbdb.snapshot import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
