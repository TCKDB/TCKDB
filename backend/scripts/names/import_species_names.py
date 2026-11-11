"""Load a reviewed species-name file into the database the DB_* environment names. Run by an operator.

Reports what it would write and writes nothing unless ``--commit`` is given. Run it where the database is
reachable with the API's own DB_* settings, from ``backend/`` with ``PYTHONPATH=.``:

    python scripts/names/import_species_names.py names_review.json              # report only
    python scripts/names/import_species_names.py names_review.json --commit     # write

Exit codes: 0 done (rows may be skipped; see the report), 2 bad arguments or file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.services.species_name_import import ReviewFileError, import_species_names


def _database_url() -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("DB_USER", "tckdb"),
        password=os.environ.get("DB_PASSWORD", "tckdb"),
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("DB_NAME", "tckdb_dev"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="import_species_names", description=__doc__.split("\n\n")[0])
    parser.add_argument("review_file", type=Path)
    parser.add_argument("--commit", action="store_true", help="write the names (default: report only)")
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.review_file.read_text())
    except (OSError, ValueError) as exc:
        print(f"cannot read the review file: {exc}", file=sys.stderr)
        return 2
    engine = create_engine(_database_url(), future=True)
    try:
        with Session(engine) as session:
            try:
                report = import_species_names(session, document, dry_run=not args.commit)
            except ReviewFileError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            if args.commit:
                session.commit()
            else:
                session.rollback()
    finally:
        engine.dispose()
    print(json.dumps(report.to_json(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
