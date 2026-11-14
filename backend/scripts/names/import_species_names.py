"""Load reviewed species names into the database the API's DB_* settings name. Run by an operator.

Reads a local file and the database; it never opens a network connection. Reports what it would write and writes
nothing unless ``--commit`` is given. Run it where the database is reachable with the API's own settings, from
``backend/``; ``/dev/stdin`` reads a JSON file from a pipe:

    python scripts/names/import_species_names.py names_review.json              # report only
    python scripts/names/import_species_names.py names_review.json --commit     # write
    python scripts/names/import_species_names.py names.csv --source "Name" --source-release R [--commit]
    python scripts/names/import_species_names.py --propose-from-cccbdb > proposals.csv

The file is the JSON review file of the PubChem name fetch, or a CSV (a ``.csv`` name, or ``--format csv``). CSV
columns: ``name``, ``kind`` (common, systematic or abbreviation), ``record_key``, ``retrieved_at`` (ISO 8601), and an
identity: ``inchikey``, or ``smiles`` with ``charge`` and ``multiplicity``, or both (they must name the same species).
A row is written only when exactly one live species matches; the rest are reported with a reason and skipped. An
InChIKey is matched as stored, blind to the stereo and isotope marks the stored key does not carry; a SMILES is
resolved the way an upload resolves it. A CSV load cites the external source ``--source`` at ``--source-release``,
created when absent (``--terms-url`` and ``--citation`` fill a new one). A report-only run creates nothing.
``--propose-from-cccbdb`` lists names held in stored CCCBDB observations as CSV for review; it writes nothing.

Exit codes: 0 done (rows may be skipped; see the report), 2 bad arguments or file.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.species_name_csv_import import (  # noqa: E402
    import_species_names_csv,
    propose_from_cccbdb,
    read_rows,
)
from app.services.species_name_import import (  # noqa: E402
    ImportReport,
    ReviewFileError,
    SourceSpec,
    import_species_names,
)

PROPOSAL_COLUMNS = ["species_ref", "inchikey", "name", "kind", "record_key", "retrieved_at"]


def _print_text(report: ImportReport, out) -> None:
    for row in report.rows:
        verb = {"inserted": "added", "would_insert": "would add"}.get(row.outcome, "skipped")
        reason = f" ({row.reason})" if row.reason else ""
        print(f"{verb}: {row.species_ref or '-'} {row.name!r} {row.kind or '-'}{reason}", file=out)
    counts = ", ".join(f"{k}={v}" for k, v in report.counts.items()) or "no rows"
    print(f"{'report only' if report.dry_run else 'import'}: {counts}", file=out)


def main(argv: list[str] | None = None, *, session: Session | None = None, out=None) -> int:
    """Run the import. ``session`` is for tests; without it the script opens the API's own database."""
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="import_species_names", description=__doc__.split("\n\n")[0])
    parser.add_argument("review_file", type=Path, nargs="?", help="a PubChem review JSON file or a reviewed CSV")
    parser.add_argument("--format", choices=("auto", "json", "csv"), default="auto")
    parser.add_argument("--commit", action="store_true", help="write the names (default: report only)")
    parser.add_argument("--source", help="CSV: name of the external source the names come from")
    parser.add_argument("--source-release", help="CSV: its release")
    parser.add_argument("--terms-url", help="CSV: terms URL for a new external source")
    parser.add_argument("--citation", help="CSV: citation text for a new external source")
    parser.add_argument("--propose-from-cccbdb", action="store_true", help="list CCCBDB names as CSV; writes nothing")
    args = parser.parse_args(argv)

    if args.propose_from_cccbdb:
        fmt = "propose"
    else:
        if args.review_file is None:
            print("a review file is required", file=sys.stderr)
            return 2
        fmt = args.format
        if fmt == "auto":
            fmt = "csv" if args.review_file.suffix.lower() == ".csv" else "json"
        if fmt == "csv" and not (args.source and args.source_release):
            print("a CSV load needs --source and --source-release", file=sys.stderr)
            return 2

    document = rows = None
    if fmt == "json":
        try:
            document = json.loads(args.review_file.read_text())
        except (OSError, ValueError, RecursionError) as exc:
            print(f"cannot read the review file: {exc}", file=sys.stderr)
            return 2
    elif fmt == "csv":
        try:
            with args.review_file.open(newline="", encoding="utf-8") as handle:
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
        if fmt == "propose":
            writer = csv.DictWriter(out, fieldnames=PROPOSAL_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(propose_from_cccbdb(session))
            return 0
        dry_run = not args.commit
        if fmt == "csv":
            spec = SourceSpec(args.source, args.source_release, args.terms_url, args.citation)
            report = import_species_names_csv(session, rows, source=spec, dry_run=dry_run)
        else:
            try:
                report = import_species_names(session, document, dry_run=dry_run)
            except ReviewFileError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        if args.commit:
            session.commit()
        if fmt == "csv":
            _print_text(report, out)
        else:
            print(json.dumps(report.to_json(), indent=2), file=out)
        return 0
    finally:
        if engine is not None:
            session.close()
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
