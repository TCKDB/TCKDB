#!/usr/bin/env python
"""CLI wrapper for the ThermoML Cp extractor.

Usage::

    conda run -n tckdb_env python -m scripts.thermoml_cp_extract \\
        --archive data/external/thermoml/ThermoML.v2020-09-30.tgz \\
        --doi 10.1016/j.fluid.2016.07.034 \\
        --out data/external/thermoml/extract

Read-only: fetches (if needed)/verifies the archive, selects and
schema-validates one article, parses it, and maps its heat-capacity
tables to ``MolecularPropertyObservationCreate`` payload JSON. Never
writes to the database -- persistence is Phase C-E3. See
``backend/app/importers/thermoml/cli.py`` for the runner.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# E402: must follow the sys.path bootstrap above so `app` resolves when the
# wrapper is run directly without PYTHONPATH.
from app.importers.thermoml.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
