"""Export a local contribution bundle to a JSON file.

Thin CLI wrapper around
:mod:`app.services.contribution_bundle_export`. The CLI handles argument
parsing, database session setup, and writing the validated bundle to
disk — all bundle-construction logic lives in the service.

Usage::

    # Thermo bundle
    conda run -n tckdb_env python scripts/export_contribution_bundle.py \\
        --kind thermo \\
        --thermo-id 1 \\
        --output ./thermo-bundle.tckdb.json \\
        --title "Example thermo contribution" \\
        --summary "Selected thermo records from local TCKDB"

    # Kinetics bundle (one or more --kinetics-id flags accepted)
    conda run -n tckdb_env python scripts/export_contribution_bundle.py \\
        --kind kinetics \\
        --kinetics-id 10 \\
        --output ./kinetics-bundle.tckdb.json \\
        --title "Example kinetics contribution" \\
        --summary "Selected kinetics records from local TCKDB"

The bundle is **not** sent anywhere. Hosted import is not implemented yet
— see ``docs/roadmaps/local-bundle-export-v0-spec.md``.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.config import settings
from app.schemas.workflows.contribution_bundle import (
    BundleSourceInstanceKind,
    ContributionBundleV0,
)
from app.services.contribution_bundle_export import (
    ContributionBundleExportError,
    DEFAULT_INSTANCE_NAME,
    export_kinetics_bundle,
    export_thermo_bundle,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--kind",
        required=True,
        choices=["thermo", "kinetics"],
        help="Bundle family to export.",
    )
    parser.add_argument(
        "--thermo-id",
        type=int,
        action="append",
        default=[],
        help="Local thermo.id to export. Repeatable.",
    )
    parser.add_argument(
        "--kinetics-id",
        type=int,
        action="append",
        default=[],
        help="Local kinetics.id to export. Repeatable.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to write the bundle JSON file to.",
    )
    parser.add_argument(
        "--title",
        required=True,
        help="Bundle submission title.",
    )
    parser.add_argument(
        "--summary",
        required=True,
        help="Bundle submission summary.",
    )
    parser.add_argument(
        "--instance-name",
        default=DEFAULT_INSTANCE_NAME,
        help=f"Source instance name (default: {DEFAULT_INSTANCE_NAME}).",
    )
    parser.add_argument(
        "--instance-kind",
        choices=[k.value for k in BundleSourceInstanceKind],
        default=BundleSourceInstanceKind.local.value,
        help="Source instance kind (default: local).",
    )
    parser.add_argument(
        "--exporter-label",
        default=None,
        help="Local exporter label (defaults to current OS user).",
    )
    parser.add_argument("--orcid", default=None)
    parser.add_argument("--affiliation", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--exporter-notes", default=None)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if args.kind == "thermo":
        if not args.thermo_id:
            raise SystemExit(
                "error: --kind thermo requires at least one --thermo-id."
            )
        if args.kinetics_id:
            raise SystemExit(
                "error: --kinetics-id is only valid with --kind kinetics."
            )
    elif args.kind == "kinetics":
        if not args.kinetics_id:
            raise SystemExit(
                "error: --kind kinetics requires at least one --kinetics-id."
            )
        if args.thermo_id:
            raise SystemExit(
                "error: --thermo-id is only valid with --kind thermo."
            )

    if args.output.exists() and not args.overwrite:
        raise SystemExit(
            f"error: output path {args.output} already exists; "
            "pass --overwrite to replace it."
        )


def _resolve_exporter_label(provided: str | None) -> str:
    if provided:
        return provided
    try:
        return getpass.getuser() or "local-user"
    except Exception:
        return "local-user"


def _write_bundle(bundle: ContributionBundleV0, output: Path) -> None:
    """Write the bundle as deterministic, UTF-8 JSON."""
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = bundle.model_dump(mode="json")
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _export(session: Session, args: argparse.Namespace) -> ContributionBundleV0:
    exporter_label = _resolve_exporter_label(args.exporter_label)
    if args.kind == "thermo":
        return export_thermo_bundle(
            session,
            thermo_ids=args.thermo_id,
            title=args.title,
            summary=args.summary,
            exporter_label=exporter_label,
            instance_name=args.instance_name,
            instance_kind=BundleSourceInstanceKind(args.instance_kind),
            orcid=args.orcid,
            affiliation=args.affiliation,
            email=args.email,
            exporter_notes=args.exporter_notes,
        )
    return export_kinetics_bundle(
        session,
        kinetics_ids=args.kinetics_id,
        title=args.title,
        summary=args.summary,
        exporter_label=exporter_label,
        instance_name=args.instance_name,
        instance_kind=BundleSourceInstanceKind(args.instance_kind),
        orcid=args.orcid,
        affiliation=args.affiliation,
        email=args.email,
        exporter_notes=args.exporter_notes,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _validate_args(args)

    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            try:
                bundle = _export(session, args)
            except ContributionBundleExportError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
    finally:
        engine.dispose()

    _write_bundle(bundle, args.output)

    record_count = (
        len(bundle.records.thermo_uploads)
        if args.kind == "thermo"
        else len(bundle.records.kinetics_uploads)
    )
    print(f"Wrote contribution bundle: {args.output}")
    print(f"Bundle kind: {bundle.bundle_kind.value}")
    print(f"Records exported: {record_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
