"""Print Phase A legacy/queue incompatibilities as NDJSON; never writes rows.

Run from backend with PYTHONPATH=. and the intended database environment.
"""

import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.config import settings
from app.services.thermo_contract_inventory import iter_thermo_contract_inventory


def main() -> None:
    engine = create_engine(settings.database_url, isolation_level="REPEATABLE READ")
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            for record in iter_thermo_contract_inventory(session):
                print(json.dumps(record, sort_keys=True, allow_nan=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
