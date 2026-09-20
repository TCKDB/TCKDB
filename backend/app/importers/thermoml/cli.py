"""CLI: fetch-verify -> select -> validate -> parse -> map, no DB writes.

Entry point for ``backend/scripts/thermoml_cp_extract.py``. Writes
payload JSON, the mapping report and the archive snapshot to
``--out``; never touches the database. Persisting the payloads is
Phase C-E3.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.importers.thermoml import ARCHIVE_URL
from app.importers.thermoml.archive import fetch_archive, select_article, write_snapshot
from app.importers.thermoml.mapping import map_document
from app.importers.thermoml.parser import parse_thermoml_document
from app.importers.thermoml.validate import ThermoMLConfigurationError, validate_bytes


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="thermoml_cp_extract",
        description=(
            "Read one ThermoML archive article's heat-capacity tables into "
            "MolecularPropertyObservationCreate payloads. Read-only: never "
            "writes to the database."
        ),
    )
    p.add_argument(
        "--archive",
        type=Path,
        required=True,
        help=(
            "Path to a local ThermoML.v2020-09-30.tgz. If it does not exist "
            f"yet, it is fetched from the single pinned URL ({ARCHIVE_URL})."
        ),
    )
    p.add_argument(
        "--doi",
        required=True,
        help="The article's own DOI, e.g. 10.1016/j.fluid.2016.07.034",
    )
    p.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory for the snapshot, payloads and mapping report.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    # fetch_archive is idempotent (reuses an already-correct file after
    # re-verifying its digest) and refuses any URL but the pinned one, so
    # always route through it rather than trusting a caller-supplied path.
    fetch_archive(ARCHIVE_URL, args.archive)

    article = select_article(args.archive, args.doi)

    try:
        schema_report = validate_bytes(article.xml)
    except ThermoMLConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not schema_report.valid:
        print("schema-invalid document:", file=sys.stderr)
        for err in schema_report.errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    document = parse_thermoml_document(article.xml)
    result = map_document(document, doi=args.doi)

    manifest = write_snapshot(article, output_dir=args.out, doi=args.doi)
    _atomic_write_json(
        args.out / "payloads.json",
        [p.model_dump(mode="json") for p in result.payloads],
    )
    _atomic_write_json(
        args.out / "mapping_report.json", result.report.model_dump(by_alias=True)
    )
    _atomic_write_json(args.out / "literature.json", result.literature.model_dump())

    print(
        f"wrote {len(result.payloads)} payload(s), "
        f"{len(result.report.rejected)} rejected, "
        f"{len(result.report.unsupported)} unsupported entries, "
        f"to {args.out} (snapshot manifest: {manifest['xml_path']})"
    )
    return 0


__all__ = ["build_arg_parser", "main"]
