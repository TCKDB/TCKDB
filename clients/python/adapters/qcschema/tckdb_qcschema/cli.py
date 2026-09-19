"""CLI entry point for the QCSchema importer (C-Q1).

    tckdb-qcschema import result.json [--smiles O] \
        [--species-entry-kind minimum] [--upload | --dry-run] \
        [--allow-duplicate] [--json]

    tckdb-qcschema report result.json [--smiles O] [--json]

``report`` runs the read + map stages only (no network, ever) and prints
the mapping report; it is what ``--dry-run`` also uses internally. Only
``import --upload`` contacts a live TCKDB instance. ``export`` (turning a
stored calculation back into a QCSchema document) is C-Q3 and is not part
of this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tckdb_schemas.enums import StationaryPointKind

from .errors import QCSchemaAdapterError
from .mapping import build_conformer_upload_payload
from .reader import read_document
from .uploader import sha256_bytes


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tckdb-qcschema")
    sub = p.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="Map a QCSchema document and (optionally) upload it.")
    imp.add_argument("file", help="Path to a QCSchema AtomicResult/OptimizationResult JSON file.")
    imp.add_argument("--smiles", help="Depositor-declared identity SMILES (source=depositor_declared).")
    imp.add_argument(
        "--species-entry-kind",
        default="minimum",
        choices=[k.value for k in StationaryPointKind],
    )
    imp.add_argument("--upload", action="store_true", help="POST to a live TCKDB API.")
    imp.add_argument("--dry-run", action="store_true", help="Build payload + keys without sending.")
    imp.add_argument("--allow-duplicate", action="store_true", help="Bypass the raw-sha256 precheck refusal.")
    imp.add_argument("--base-url", help="TCKDB base URL (else $TCKDB_BASE_URL).")
    imp.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")

    rep = sub.add_parser("report", help="Read + map only; print the mapping report. Never touches the network.")
    rep.add_argument("file", help="Path to a QCSchema AtomicResult/OptimizationResult JSON file.")
    rep.add_argument("--smiles", help="Depositor-declared identity SMILES (source=depositor_declared).")
    rep.add_argument(
        "--species-entry-kind",
        default="minimum",
        choices=[k.value for k in StationaryPointKind],
    )
    rep.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")

    return p


def _map_file(path: Path, *, smiles: str | None, species_entry_kind: str):
    raw_bytes = path.read_bytes()
    record = read_document(raw_bytes)
    raw_sha256 = sha256_bytes(raw_bytes)
    payload, report = build_conformer_upload_payload(
        record,
        raw_bytes=raw_bytes,
        raw_artifact_filename=f"{path.stem}.qcschema.json",
        raw_artifact_sha256=raw_sha256,
        declared_smiles=smiles,
        species_entry_kind=StationaryPointKind(species_entry_kind),
    )
    return raw_bytes, raw_sha256, record, payload, report


def _cmd_report(args: argparse.Namespace) -> int:
    path = Path(args.file)
    try:
        _raw_bytes, raw_sha256, record, payload, report = _map_file(
            path, smiles=args.smiles, species_entry_kind=args.species_entry_kind
        )
    except QCSchemaAdapterError as exc:
        return _emit_error(exc, as_json=args.json)

    result = {
        "family": record.family,
        "record_kind": record.record_kind,
        "canonical_document_sha256": record.canonical_sha256,
        "raw_sha256": raw_sha256,
        "calculation_type": payload["calculation"]["type"],
        "mapping_report": report.to_dict(),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"family={result['family']} record_kind={result['record_kind']}")
        print(f"calculation.type={result['calculation_type']}")
        print(f"canonical_document_sha256={result['canonical_document_sha256']}")
        for bucket, paths in result["mapping_report"].items():
            print(f"  {bucket}: {', '.join(paths) if paths else '(none)'}")
    return 0


def _emit_error(exc: QCSchemaAdapterError, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"error_code": exc.code, "message": exc.message}), file=sys.stderr)
    else:
        print(f"REFUSED [{exc.code}]: {exc.message}", file=sys.stderr)
    return 2


def _cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.file)
    try:
        raw_bytes, raw_sha256, record, payload, report = _map_file(
            path, smiles=args.smiles, species_entry_kind=args.species_entry_kind
        )
    except QCSchemaAdapterError as exc:
        return _emit_error(exc, as_json=args.json)

    if not (args.upload or args.dry_run):
        # Same as `report`: build + validate, print, never touch the network.
        return _cmd_report(args)

    from .uploader import upload_record  # lazy: report/map stay client-free

    if args.dry_run:
        outcome = upload_record(
            client=None,
            record=record,
            payload=payload,
            raw_bytes=raw_bytes,
            raw_sha256=raw_sha256,
            artifact_filename=f"{path.stem}.qcschema.json",
            allow_duplicate=args.allow_duplicate,
            dry_run=True,
        )
        if args.json:
            print(json.dumps(outcome, indent=2))
        else:
            print(f"DRY RUN: would POST {outcome['calculation_type']} conformer")
            print(f"  conformers key: {outcome['conformers_idempotency_key']}")
            print(f"  artifact key:   {outcome['artifact_idempotency_key']}")
        return 0

    import os

    from tckdb_client import TCKDBClient  # lazy

    base_url = args.base_url or os.environ.get("TCKDB_BASE_URL")
    api_key = os.environ.get("TCKDB_API_KEY")
    try:
        with TCKDBClient(base_url, api_key=api_key) as client:
            outcome = upload_record(
                client=client,
                record=record,
                payload=payload,
                raw_bytes=raw_bytes,
                raw_sha256=raw_sha256,
                artifact_filename=f"{path.stem}.qcschema.json",
                allow_duplicate=args.allow_duplicate,
                dry_run=False,
            )
    except QCSchemaAdapterError as exc:
        return _emit_error(exc, as_json=args.json)

    result = {
        "calculation_id": outcome.calculation_id,
        "conformer_replayed": outcome.conformer_replayed,
        "artifact_replayed": outcome.artifact_replayed,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"calculation_id={result['calculation_id']}")
        print(f"conformer_replayed={result['conformer_replayed']}")
        print(f"artifact_replayed={result['artifact_replayed']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "report":
        return _cmd_report(args)
    if args.command == "import":
        return _cmd_import(args)
    raise AssertionError(f"unreachable: unknown command {args.command!r}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
