"""CLI entry point for the CHEMKIN importer (M4/M5).

    tckdb-chemkin-import chem.inp --thermo therm.dat --transport tran.dat \
        --species-dict species_dictionary.txt --species-map map.csv \
        --mechanism-name GRI-Mech --mechanism-version 3.0 \
        [--dry-run | --upload] [--base-url URL] [--json]

Stages 1-4 run offline; only ``--upload`` contacts a live TCKDB instance.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .identity import IdentityResolutionError, IdentityResolver, parse_species_dictionary, parse_species_map_csv
from .normalizer import normalize_mechanism
from .parser import parse_mechanism, parse_thermo_file
from .payloads import ImportConfig, build_all_payloads
from .transport import parse_transport_file


def _read(path: str | None) -> str | None:
    return Path(path).read_text() if path else None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tckdb-chemkin-import")
    p.add_argument("mechanism", help="CHEMKIN mechanism file (chem.inp)")
    p.add_argument("--thermo", help="Separate NASA-7 thermo file (therm.dat)")
    p.add_argument("--transport", help="Transport file (tran.dat)")
    p.add_argument("--species-dict", help="RMG species_dictionary.txt")
    p.add_argument("--species-map", help="CSV map: name,smiles[,charge,multiplicity]")
    p.add_argument("--mechanism-name", help="Mechanism name (workflow_tool_release)")
    p.add_argument("--mechanism-version", help="Mechanism version")
    p.add_argument("--mechanism-id", help="Stable id for idempotency (default: name)")
    p.add_argument(
        "--scientific-origin",
        default="experimental",
        choices=["experimental", "estimated", "computed"],
    )
    p.add_argument("--allow-pseudo", action="store_true",
                   help="Map unresolved names to pseudo species (needs a source note).")
    p.add_argument("--upload", action="store_true", help="POST to a live TCKDB API.")
    p.add_argument("--dry-run", action="store_true",
                   help="Build payloads + idempotency keys without sending.")
    p.add_argument("--base-url", help="TCKDB base URL (else $TCKDB_BASE_URL).")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    mech = parse_mechanism(_read(args.mechanism), thermo_text=_read(args.thermo))
    if args.transport:
        mech.transport = parse_transport_file(_read(args.transport))

    resolver = IdentityResolver(allow_pseudo=args.allow_pseudo)
    if args.species_map:
        resolver.csv_map = parse_species_map_csv(_read(args.species_map))
    if args.species_dict:
        resolver.rmg_dict = parse_species_dictionary(_read(args.species_dict))

    config = ImportConfig(
        scientific_origin=args.scientific_origin,
        mechanism_name=args.mechanism_name,
        mechanism_version=args.mechanism_version,
    )

    try:
        normalized = normalize_mechanism(mech)
        payloads = build_all_payloads(mech, resolver, config, normalized=normalized)
    except IdentityResolutionError as exc:
        print(f"IDENTITY ERROR: {exc}", file=sys.stderr)
        return 2

    summary = {
        "counts": payloads.counts(),
        "warnings": payloads.warnings,
    }

    if args.upload or args.dry_run:
        from tckdb_client import TCKDBClient  # lazy

        mech_id = args.mechanism_id or args.mechanism_name or Path(args.mechanism).stem
        from .uploader import upload_payloads

        if args.dry_run:
            report = upload_payloads(payloads, client=None, mechanism_id=mech_id, dry_run=True)
        else:
            import os

            base_url = args.base_url or os.environ.get("TCKDB_BASE_URL")
            api_key = os.environ.get("TCKDB_API_KEY")
            with TCKDBClient(base_url, api_key=api_key) as client:
                report = upload_payloads(payloads, client=client, mechanism_id=mech_id)
        summary["upload"] = report.summary()

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Parsed {len(mech.species)} species, {len(mech.reactions)} reactions.")
        print(f"Payloads: {payloads.counts()}")
        for w in payloads.warnings:
            print(f"  WARN: {w}")
        if "upload" in summary:
            print(f"Upload: {summary['upload']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
