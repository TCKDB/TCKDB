"""CLI: import CCCBDB ``MolecularPropertyObservationCreate`` payloads
from the flat and form dry-run output directories into the database.

Default behavior is a dry-run preview — every payload is validated,
identity resolution runs against the current DB state, and would-be
inserts are counted, but **no rows are committed**. Pass ``--commit``
to persist.

Exit codes:

    0 — dry-run or commit finished. Inspect the printed summary +
        ``dispositions`` for per-row outcomes.
    2 — argument / configuration error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.importers.cccbdb.payload_io import (
    filter_payloads_by_property_kind,
    load_payloads,
)
from app.services.cccbdb_molecular_property_import import (
    import_cccbdb_molecular_property_payloads,
)


_logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cccbdb_import_molecular_property_payloads",
        description=(
            "Import validated CCCBDB MolecularPropertyObservationCreate "
            "payloads (from the flat and/or form dry-run lanes) into the "
            "database with conservative identity resolution and "
            "idempotency."
        ),
    )
    p.add_argument(
        "--flat-payload-dir", type=Path, default=None,
        help=(
            "Path to the property-table dry-run output "
            "(payloads_dryrun/). One ``*.json`` per property_kind."
        ),
    )
    p.add_argument(
        "--form-payload-dir", type=Path, default=None,
        help=(
            "Path to the form-resolver dry-run output "
            "(form_payloads_dryrun/). One ``*.json`` per target_kind."
        ),
    )
    p.add_argument(
        "--property-kind", action="append", default=None,
        help=(
            "Restrict import to one property_kind. Repeatable. Defaults "
            "to every kind found in the loaded payload files."
        ),
    )
    p.add_argument(
        "--commit", action="store_true",
        help="Actually persist rows. Default is dry-run.",
    )
    p.add_argument(
        "--no-resolve-identity", action="store_true",
        help=(
            "Skip auto-resolution of species_entry_id. All rows are "
            "inserted with species_entry_id=NULL."
        ),
    )
    p.add_argument(
        "--created-by", type=int, default=None,
        help="Optional user id to record on every inserted row.",
    )
    p.add_argument(
        "--fail-on-invalid", action="store_true",
        help=(
            "Raise on the first payload that fails pydantic validation. "
            "Default is to record an ``invalid`` disposition and continue."
        ),
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N payloads (after filtering).",
    )
    p.add_argument(
        "--summary-path", type=Path, default=None,
        help=(
            "Optional path to write the full result + dispositions JSON. "
            "Always printed to stdout in summary form regardless."
        ),
    )
    return p


def _database_url() -> str:
    """Compose the SQLAlchemy URL from the same env vars Alembic uses."""

    user = os.environ.get("DB_USER", "tckdb")
    password = os.environ.get("DB_PASSWORD", "tckdb")
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "tckdb_dev")
    return (
        f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
        "?client_encoding=utf8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.flat_payload_dir is None and args.form_payload_dir is None:
        _logger.error(
            "must supply at least one of --flat-payload-dir / "
            "--form-payload-dir"
        )
        return 2
    for dir_arg in (args.flat_payload_dir, args.form_payload_dir):
        if dir_arg is not None and not dir_arg.exists():
            _logger.error("payload directory does not exist: %s", dir_arg)
            return 2

    loaded = load_payloads(
        flat_payload_dir=args.flat_payload_dir,
        form_payload_dir=args.form_payload_dir,
    )
    loaded = filter_payloads_by_property_kind(loaded, args.property_kind)
    if args.limit is not None:
        loaded = loaded[: args.limit]
    payloads = [lp.payload for lp in loaded]
    source_paths = [str(lp.source_path) for lp in loaded]

    engine = create_engine(_database_url(), future=True)
    with Session(engine) as session:
        result = import_cccbdb_molecular_property_payloads(
            session,
            payloads,
            commit=args.commit,
            resolve_identity=not args.no_resolve_identity,
            created_by=args.created_by,
            fail_on_invalid=args.fail_on_invalid,
            source_paths=source_paths,
        )

    payload_files_read = len(
        {(lp.lane, str(lp.source_path)) for lp in loaded}
    )
    result.payload_files_read = payload_files_read

    summary = {
        "commit": args.commit,
        "payload_files_read": payload_files_read,
        **result.to_json(),
    }
    summary_text = json.dumps(summary, indent=2, sort_keys=True)
    if args.summary_path is not None:
        args.summary_path.write_text(summary_text + "\n", encoding="utf-8")
    print(summary_text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
