#!/usr/bin/env python
"""CLI wrapper for the CCCBDB property-table dry-run exporter.

Usage::

    conda run -n tckdb_env python -m scripts.cccbdb_property_payload_dryrun \\
        --archive-dir data/external/cccbdb \\
        --output-dir data/external/cccbdb/payloads_dryrun \\
        --use-cache-only

Never writes to the database. See
``backend/app/importers/cccbdb/property_payload_dryrun.py`` for the
runner.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importers.cccbdb.property_payload_dryrun import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
