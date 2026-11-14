"""Load a reviewed CSV of species names into the database the API's DB_* settings name. Run by an operator.

The script reads a local file and the database. It never opens a network connection.

    python scripts/import_species_names.py --file reviewed.csv --source "Source name" --dry-run
    python scripts/import_species_names.py --file reviewed.csv --source "Source name" [--source-release R]
    python scripts/import_species_names.py --propose-from-cccbdb > proposals.csv

CSV columns: ``name``, ``kind`` (common, systematic or abbreviation), and an identity: ``inchikey``, or ``smiles``
with ``charge`` and ``multiplicity``. Optional: ``record_key``, ``retrieved_at``. A row is written only when exactly
one live species matches; otherwise it is reported and skipped. ``--source`` names an existing ``external_source``.
``--propose-from-cccbdb`` lists names held in stored CCCBDB observations as CSV for a curator to review; it writes
nothing.

Exit codes: 0 done (rows may be skipped; see the report), 2 bad arguments, file or source.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.species_name_csv_import import (  # noqa: E402
    SourceError,
    import_species_names_csv,
    propose_from_cccbdb,
    read_rows,
    resolve_source,
)


def _print_report(report, out) -> None:
    for row in report.rows:
        verb = {"inserted": "added", "would_insert": "would add"}.get(row.outcome, "skipped")
        reason = f" ({row.reason})" if row.reason else ""
        print(f"{verb}: {row.species_ref or '-'} {row.name!r} {row.kind or '-'}{reason}", file=out)
    counts = ", ".join(f"{k}={v}" for k, v in report.counts.items()) or "no rows"
    print(f"{'dry run' if report.dry_run else 'import'}: {counts}", file=out)


def main(argv: list[str] | None = None, *, session: Session | None = None, out=None) -> int:
    """Run the import. ``session`` is for tests; without it the script opens the API's own database."""
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="import_species_names", description=__doc__.split("\n\n")[0])
    parser.add_argument("--file", type=Path, help="the reviewed CSV")
    parser.add_argument("--source", help="source_name of an existing external_source")
    parser.add_argument("--source-release", help="its release, when the name has several")
    parser.add_argument("--dry-run", action="store_true", help="report what would be written and write nothing")
    parser.add_argument("--propose-from-cccbdb", action="store_true", help="list CCCBDB names as CSV; writes nothing")
    args = parser.parse_args(argv)
    if not args.propose_from_cccbdb and (args.file is None or args.source is None):
        parser.print_usage(sys.stderr)
        print("--file and --source are required", file=sys.stderr)
        return 2

    rows = []
    if not args.propose_from_cccbdb:
        try:
            with args.file.open(newline="", encoding="utf-8") as handle:
                rows = read_rows(handle)
        except (OSError, ValueError, csv.Error) as exc:
            print(f"cannot read the CSV: {exc}", file=sys.stderr)
            return 2

    engine = None
    if session is None:
        from app.api.config import settings

        engine = create_engine(settings.database_url, future=True)
        session = Session(engine)
    try:
        if args.propose_from_cccbdb:
            writer = csv.DictWriter(out, fieldnames=["species_ref", "inchikey", "name", "kind"], lineterminator="\n")
            writer.writeheader()
            writer.writerows(propose_from_cccbdb(session))
            return 0
        try:
            source = resolve_source(session, name=args.source, release=args.source_release)
        except SourceError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        report = import_species_names_csv(
            session, rows, source=source, record_prefix=args.file.name, dry_run=args.dry_run
        )
        if not args.dry_run:
            session.commit()
        _print_report(report, out)
        return 0
    finally:
        if engine is not None:
            session.close()
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
