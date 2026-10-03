"""Print how many stored thermo records can answer H298 and why the rest cannot (JSON). Read-only.

Writes nothing and backfills nothing: the transaction is opened READ ONLY, so a database
refuses any write this script (or a later edit of it) tries to make. Run from ``backend`` with
``PYTHONPATH=.`` and the intended database environment, for example::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/thermo_h298_coverage_inventory.py

Use it to see how much of the corpus method-aware H298 selection can act on, and which missing
declarations (enthalpy reference, thermodynamic target, protocol) hold the rest back.
"""

from __future__ import annotations

import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.config import settings
from app.services.thermo_selection.inventory import h298_coverage_inventory


def main() -> None:
    engine = create_engine(settings.database_url, isolation_level="REPEATABLE READ")
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            print(json.dumps(h298_coverage_inventory(session), indent=2, sort_keys=True, allow_nan=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
