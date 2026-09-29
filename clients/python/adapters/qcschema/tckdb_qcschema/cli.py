"""CLI entry point for the QCSchema importer and exporter (C-Q1, C-Q3).

    tckdb-qcschema import result.json [--smiles O] \
        [--species-entry-kind minimum] [--upload | --dry-run] \
        [--allow-duplicate] [--json]

    tckdb-qcschema report result.json [--smiles O] [--json]

    tckdb-qcschema import drive.json [drive2.json ...] --parent-opt opt.json [--smiles OO] \
        [--upload | --dry-run] [--allow-duplicate] [--json]

    tckdb-qcschema report result.json [--smiles O] [--json]

    tckdb-qcschema export <calculation_ref_or_id> [--out FILE] [--json]

``report`` runs the read + map stages only (no network, ever) and prints
the mapping report; it is what ``--dry-run`` also uses internally. ``import
--upload`` and ``export`` are the only commands that contact a live TCKDB
instance -- see :mod:`tckdb_qcschema.exporter` for what ``export`` supports
and refuses.

A ``TorsionDriveResult`` (QCSchema's scan) is imported together with the
``OptimizationResult`` it started from (``--parent-opt``): TCKDB attaches a
scan to a conformer, and the conformer is anchored by that optimization.
Every drive from one optimization (one per rotor, say) is named in the
same import; they all go up with it as one ``POST /uploads/computed-species``
bundle, because a later bundle could not reach the conformer this one
creates and would store the optimization twice -- see
:mod:`tckdb_qcschema.scan`. ``export`` of a stored ``scan`` produces a v2
``TorsionDriveResult`` when the scan is one, with what was not carried
listed in ``extras.tckdb.export_report`` -- see
:mod:`tckdb_qcschema.scan_export`.

Exit codes: 0 success, 2 an adapter refusal (``REFUSED [code]: ...``), 1 the
TCKDB API could not be used -- a transport failure, an HTTP error, or a
response that is not the API's (``ERROR [...]: ...``; e.g. a ``--base-url``
naming the web site rather than its ``/api/v1`` root). Neither prints a
traceback.
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


_PARENT_OPT_HELP = (
    "TorsionDriveResult only: the OptimizationResult the drive started "
    "from. It becomes the conformer's opt and the scan is attached to it; "
    "it must share the drive's atoms, charge and multiplicity, and its "
    "final geometry must be one of the drive's initial molecules."
)


_FILE_HELP = (
    "Path to a QCSchema AtomicResult/OptimizationResult/TorsionDriveResult "
    "JSON file. Several TorsionDriveResult files may be given together: "
    "every drive that started from the same --parent-opt goes up in one "
    "bundle."
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tckdb-qcschema")
    sub = p.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="Map a QCSchema document and (optionally) upload it.")
    imp.add_argument("file", nargs="+", help=_FILE_HELP)
    imp.add_argument("--smiles", help="Depositor-declared identity SMILES (source=depositor_declared).")
    imp.add_argument("--parent-opt", help=_PARENT_OPT_HELP)
    imp.add_argument(
        "--species-entry-kind",
        default="minimum",
        choices=[k.value for k in StationaryPointKind],
    )
    imp.add_argument("--upload", action="store_true", help="POST to a live TCKDB API.")
    imp.add_argument("--dry-run", action="store_true", help="Build payload + keys without sending.")
    imp.add_argument(
        "--allow-duplicate",
        action="store_true",
        help=(
            "Bypass the raw-sha256 precheck refusal AND mint a fresh "
            "per-run idempotency-key nonce, so this run creates a genuine "
            "second deposit instead of replaying the first one's response."
        ),
    )
    imp.add_argument("--base-url", help="TCKDB base URL (else $TCKDB_BASE_URL).")
    imp.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")

    rep = sub.add_parser("report", help="Read + map only; print the mapping report. Never touches the network.")
    rep.add_argument("file", nargs="+", help=_FILE_HELP)
    rep.add_argument("--smiles", help="Depositor-declared identity SMILES (source=depositor_declared).")
    rep.add_argument("--parent-opt", help=_PARENT_OPT_HELP)
    rep.add_argument(
        "--species-entry-kind",
        default="minimum",
        choices=[k.value for k in StationaryPointKind],
    )
    rep.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")

    exp = sub.add_parser(
        "export",
        help=(
            "Read a stored sp or freq calculation back out as a QCSchema "
            "v2 AtomicResult document, or a stored relaxed dihedral scan "
            "as a v2 TorsionDriveResult (bond/angle/rigid scans and IRCs "
            "are refused: QCSchema has no model for them)."
        ),
    )
    exp.add_argument(
        "calculation_ref",
        help=(
            "A calc_... public ref, or an integer calculation_id. An "
            "integer is required to export a freq (Hessian) record on a "
            "deployment whose internal-id visibility policy hides "
            "calculation_id from the scientific read -- see "
            "tckdb_qcschema.exporter's module docstring."
        ),
    )
    exp.add_argument("--out", help="Write the document to this path instead of stdout.")
    exp.add_argument("--base-url", help="TCKDB base URL (else $TCKDB_BASE_URL).")
    exp.add_argument(
        "--json",
        action="store_true",
        help="Emit the document compactly (no indent) when printed to stdout.",
    )

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
    path = Path(args.file[0])
    if _is_torsion_drive(path):
        return _cmd_report_scan(args)
    if len(args.file) > 1:
        return _emit_error(_several_non_drives(), as_json=args.json)
    if args.parent_opt:
        return _emit_error(_parent_opt_misused(), as_json=args.json)
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


def _emit_client_error(exc: Exception, *, as_json: bool) -> int:
    """Print a ``tckdb_client`` failure as one line, the way refusals are.

    Not a refusal -- the adapter did not reject the data, it could not get
    a usable answer from the API -- so it says ``ERROR`` and exits 1. The
    bracket carries the server's error code when there is one, else the
    client's exception class, so the line is still matchable.
    """
    code = getattr(exc, "code", None) or type(exc).__name__
    if as_json:
        print(json.dumps({"error_code": code, "message": str(exc)}), file=sys.stderr)
    else:
        print(f"ERROR [{code}]: {exc}", file=sys.stderr)
    return 1


def _cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.file[0])
    if _is_torsion_drive(path):
        return _cmd_import_scan(args)
    if len(args.file) > 1:
        return _emit_error(_several_non_drives(), as_json=args.json)
    if args.parent_opt:
        return _emit_error(_parent_opt_misused(), as_json=args.json)
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

    from tckdb_client import TCKDBClient, TCKDBError  # lazy

    base_url = args.base_url or os.environ.get("TCKDB_BASE_URL")
    if not base_url:
        return _emit_missing_base_url(as_json=args.json)
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
    except TCKDBError as exc:
        return _emit_client_error(exc, as_json=args.json)

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


def _cmd_export(args: argparse.Namespace) -> int:
    import os

    from tckdb_client import TCKDBClient, TCKDBError  # lazy

    from .exporter import export_calculation

    calculation_ref = args.calculation_ref
    if calculation_ref.isdigit():
        calculation_ref = int(calculation_ref)

    base_url = args.base_url or os.environ.get("TCKDB_BASE_URL")
    if not base_url:
        return _emit_missing_base_url(as_json=args.json)
    api_key = os.environ.get("TCKDB_API_KEY")
    try:
        with TCKDBClient(base_url, api_key=api_key) as client:
            document = export_calculation(client, calculation_ref)
    except QCSchemaAdapterError as exc:
        return _emit_error(exc, as_json=args.json)
    except TCKDBError as exc:
        return _emit_client_error(exc, as_json=args.json)

    text = json.dumps(document) if args.json else json.dumps(document, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def _emit_missing_base_url(*, as_json: bool) -> int:
    """No ``--base-url`` and no ``$TCKDB_BASE_URL``: one ERROR line, exit 1.

    Checked before the client is built -- ``TCKDBClient`` itself raises a
    bare ``ValueError`` for an empty base URL, which is not a
    ``TCKDBError`` and used to end in a traceback.
    """
    message = (
        "no TCKDB base URL: pass --base-url or set $TCKDB_BASE_URL to the "
        "API root (e.g. https://<host>/api/v1)."
    )
    if as_json:
        print(json.dumps({"error_code": "missing_base_url", "message": message}), file=sys.stderr)
    else:
        print(f"ERROR [missing_base_url]: {message}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Scans (TorsionDriveResult + its parent OptimizationResult)
# ---------------------------------------------------------------------------


def _is_torsion_drive(path: Path) -> bool:
    """Whether ``path`` reads as a TorsionDriveResult.

    Any failure to read it answers False, so the ordinary path runs and
    reports that failure with its own code.
    """
    try:
        return read_document(path.read_bytes()).record_kind == "torsion_drive"
    except (QCSchemaAdapterError, OSError):
        return False


def _parent_opt_misused() -> QCSchemaAdapterError:
    from .errors import E_SCAN_PARENT_MISMATCH

    return QCSchemaAdapterError(
        E_SCAN_PARENT_MISMATCH,
        "--parent-opt applies only to a TorsionDriveResult document; this "
        "document is not one, so the option would be silently ignored.",
    )


def _several_non_drives() -> QCSchemaAdapterError:
    from .errors import E_MULTIPLE_DOCUMENTS_UNSUPPORTED

    return QCSchemaAdapterError(
        E_MULTIPLE_DOCUMENTS_UNSUPPORTED,
        "several files were given, but only TorsionDriveResult documents "
        "sharing one --parent-opt are imported together; import any other "
        "document on its own.",
    )


def _map_scan_file(args: argparse.Namespace):
    from .scan import DriveDocument, build_scan_bundle_payload

    documents = []
    for name in args.file:
        path = Path(name)
        raw = path.read_bytes()
        documents.append(DriveDocument(read_document(raw), raw, f"{path.stem}.qcschema.json", sha256_bytes(raw)))
    first, *additional = documents
    record, raw_bytes, raw_sha256 = first.record, first.raw_bytes, first.sha256
    parent_record = parent_raw = parent_sha = parent_path = None
    if args.parent_opt:
        parent_path = Path(args.parent_opt)
        parent_raw = parent_path.read_bytes()
        parent_record = read_document(parent_raw)
        parent_sha = sha256_bytes(parent_raw)
    bundle = build_scan_bundle_payload(
        record,
        raw_bytes=raw_bytes,
        raw_artifact_filename=first.filename,
        raw_artifact_sha256=raw_sha256,
        parent_record=parent_record,
        parent_raw_bytes=parent_raw,
        parent_artifact_filename=f"{parent_path.stem}.qcschema.json" if parent_path else None,
        parent_artifact_sha256=parent_sha,
        declared_smiles=args.smiles,
        species_entry_kind=StationaryPointKind(args.species_entry_kind),
        additional_drives=additional,
    )
    return documents, parent_raw, parent_sha, parent_record, bundle


def _scan_summary(documents, parent_record, bundle) -> dict:
    """The first drive at the top level; every drive under ``drives``."""
    drives = [
        {
            "file": document.filename,
            "family": document.record.family,
            "canonical_document_sha256": document.record.canonical_sha256,
            "raw_sha256": document.sha256,
            "calculation_key": mapped.key,
            "dimension": mapped.dimension,
            "point_count": mapped.point_count,
            "mapping_report": mapped.report.to_dict(),
        }
        for document, mapped in zip(documents, bundle.drives, strict=True)
    ]
    first = drives[0]
    return {
        "family": first["family"],
        "record_kind": "torsion_drive",
        "canonical_document_sha256": first["canonical_document_sha256"],
        "raw_sha256": first["raw_sha256"],
        "parent_opt_canonical_document_sha256": parent_record.canonical_sha256,
        "calculation_type": "scan",
        "dimension": first["dimension"],
        "point_count": first["point_count"],
        "mapping_report": first["mapping_report"],
        "parent_opt_mapping_report": bundle.parent_report.to_dict(),
        "drives": drives,
    }


def _print_scan_summary(result: dict) -> None:
    print(f"family={result['family']} record_kind={result['record_kind']}")
    print(
        f"calculation.type=scan dimension={result['dimension']} "
        f"points={result['point_count']} (attached to the parent opt)"
    )
    print(f"canonical_document_sha256={result['canonical_document_sha256']}")
    print(f"parent_opt_canonical_document_sha256={result['parent_opt_canonical_document_sha256']}")
    for drive in result["drives"]:
        print(f"drive {drive['file']}: dimension={drive['dimension']} points={drive['point_count']}")
        for bucket, paths in drive["mapping_report"].items():
            print(f"  {bucket}: {', '.join(paths) if paths else '(none)'}")


def _cmd_report_scan(args: argparse.Namespace) -> int:
    try:
        documents, _praw, _psha, parent_record, bundle = _map_scan_file(args)
    except QCSchemaAdapterError as exc:
        return _emit_error(exc, as_json=args.json)
    result = _scan_summary(documents, parent_record, bundle)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_scan_summary(result)
    return 0


def _cmd_import_scan(args: argparse.Namespace) -> int:
    try:
        documents, parent_raw, parent_sha, parent_record, bundle = _map_scan_file(args)
    except QCSchemaAdapterError as exc:
        return _emit_error(exc, as_json=args.json)

    if not (args.upload or args.dry_run):
        return _cmd_report_scan(args)

    from .uploader import upload_scan_bundle  # lazy: report/map stay client-free

    first, *additional = documents
    upload_kwargs = dict(
        record=first.record,
        parent_record=parent_record,
        payload=bundle.payload,
        raw_bytes=first.raw_bytes,
        raw_sha256=first.sha256,
        parent_raw_bytes=parent_raw,
        parent_raw_sha256=parent_sha,
        allow_duplicate=args.allow_duplicate,
        additional_drives=[(d.record, d.raw_bytes, d.sha256) for d in additional],
    )
    if args.dry_run:
        try:
            outcome = upload_scan_bundle(None, dry_run=True, **upload_kwargs)
        except QCSchemaAdapterError as exc:
            return _emit_error(exc, as_json=args.json)
        if args.json:
            print(json.dumps(outcome, indent=2))
        else:
            print(f"DRY RUN: would POST a computed-species bundle (opt + {outcome['drive_count']} scan(s))")
            print(f"  computed-species key: {outcome['computed_species_idempotency_key']}")
        return 0

    import os

    from tckdb_client import TCKDBClient, TCKDBError  # lazy

    base_url = args.base_url or os.environ.get("TCKDB_BASE_URL")
    if not base_url:
        return _emit_missing_base_url(as_json=args.json)
    api_key = os.environ.get("TCKDB_API_KEY")
    try:
        with TCKDBClient(base_url, api_key=api_key) as client:
            outcome = upload_scan_bundle(client, **upload_kwargs)
    except QCSchemaAdapterError as exc:
        return _emit_error(exc, as_json=args.json)
    except TCKDBError as exc:
        return _emit_client_error(exc, as_json=args.json)

    result = {
        "scan_calculation_ids": outcome.scan_calculation_ids,
        "opt_calculation_id": outcome.opt_calculation_id,
        "replayed": outcome.replayed,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            print(f"{key}={value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "report":
        return _cmd_report(args)
    if args.command == "import":
        return _cmd_import(args)
    if args.command == "export":
        return _cmd_export(args)
    raise AssertionError(f"unreachable: unknown command {args.command!r}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
