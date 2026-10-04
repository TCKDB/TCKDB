"""Print the network-selection coverage inventory as JSON; never writes rows.

Run from ``backend`` with ``PYTHONPATH=.`` and the intended database environment. The report counts, over every
stored network solve, fit and determination, what each states and leaves unstated (target, grouping, domain,
protocol). It runs in one read-only REPEATABLE READ snapshot and the database named below is printed first, so the
operator sees which database the numbers describe. Pointing it at a deployed database is the operator's act: this
repository's tooling and its tests never do.
"""

import json
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.config import settings
from app.services.network_selection.inventory import network_coverage_inventory


def main() -> None:
    engine = create_engine(settings.database_url, isolation_level="REPEATABLE READ")
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            database = session.scalar(text("SELECT current_database()"))
            print(f"network coverage inventory of database {database!r} (read-only snapshot)", file=sys.stderr)
            print(json.dumps(network_coverage_inventory(session), indent=2, sort_keys=True, allow_nan=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
