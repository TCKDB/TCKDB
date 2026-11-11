"""Load a reviewed species-name file into the database the API's DB_* settings name. Run by an operator.

Reports what it would write and writes nothing unless ``--commit`` is given. Run it where the database is reachable
with the API's own settings, from ``backend/``; ``/dev/stdin`` reads the file from a pipe:

    python scripts/names/import_species_names.py names_review.json              # report only
    python scripts/names/import_species_names.py names_review.json --commit     # write

Exit codes: 0 done (rows may be skipped; see the report), 2 bad arguments or file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.config import settings  # noqa: E402
from app.services.species_name_import import ReviewFileError, import_species_names  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="import_species_names", description=__doc__.split("\n\n")[0])
    parser.add_argument("review_file", type=Path)
    parser.add_argument("--commit", action="store_true", help="write the names (default: report only)")
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.review_file.read_text())
    except (OSError, ValueError, RecursionError) as exc:
        print(f"cannot read the review file: {exc}", file=sys.stderr)
        return 2
    engine = create_engine(settings.database_url, future=True)
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
