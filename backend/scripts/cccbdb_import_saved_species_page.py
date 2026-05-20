#!/usr/bin/env python
"""CLI wrapper for browser-assisted CCCBDB species page import.

Usage:

    conda run -n tckdb_env python -m scripts.cccbdb_import_saved_species_page \\
        --input-html /path/to/browser_saved_h2o.html \\
        --output-dir data/external/cccbdb \\
        --species-key h2o \\
        --source-url "https://cccbdb.nist.gov/..." \\
        --cas-number 7732-18-5

Never touches the network. See
``backend/app/importers/cccbdb/browser_import.py`` for the runner.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importers.cccbdb.browser_import import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
