"""CLI entrypoint: parse an Arkane PDep run into a NetworkPDepUploadRequest.

Dry-run only -- never touches a database or server. Emits the validated
payload as JSON (or, with ``--no-validate``, the raw dict) plus a coverage
report on stderr.

Usage::

    conda run -n tckdb_env python -m scripts.pdep_ingestion.cli RUN_DIR \
        [--out payload.json] [--artifacts] [--no-validate]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import build_network_pdep_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Arkane run directory")
    parser.add_argument("--out", type=Path, default=None, help="Write JSON payload here")
    parser.add_argument(
        "--artifacts", action="store_true", help="Embed ESS log files as artifacts"
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip Pydantic validation (emit raw dict; no app import)",
    )
    args = parser.parse_args(argv)

    payload, gap = build_network_pdep_payload(
        args.run_dir, include_artifacts=args.artifacts
    )

    # Coverage / gap report on stderr.
    print("=== PDep ingestion coverage ===", file=sys.stderr)
    print(f"species built  ({len(gap.species_built)}): {gap.species_built}", file=sys.stderr)
    if gap.species_skipped:
        print(f"species skipped: {gap.species_skipped}", file=sys.stderr)
    print(f"TS full ab-initio ({len(gap.ts_built)}): {gap.ts_built}", file=sys.stderr)
    if gap.ts_stub_no_geometry:
        print(f"TS stubs (no geometry -> micro_reaction only): {gap.ts_stub_no_geometry}", file=sys.stderr)
    print(f"species with statmech ({len(gap.species_with_statmech)}): {gap.species_with_statmech}", file=sys.stderr)
    print(f"torsions emitted for: {gap.torsions_emitted}", file=sys.stderr)
    print(f"micro_reactions: {gap.micro_reactions}", file=sys.stderr)
    print(f"channels built: {gap.channels_built}", file=sys.stderr)
    if gap.channels_unmapped:
        print(f"channels unmapped: {gap.channels_unmapped}", file=sys.stderr)
    if gap.channels_duplicate:
        print(f"channels duplicate (skipped): {gap.channels_duplicate}", file=sys.stderr)
    if gap.pdep_non_chebyshev:
        print(f"pdepreaction non-Chebyshev (skipped): {gap.pdep_non_chebyshev}", file=sys.stderr)
    print(f"states: {len(payload['states'])}", file=sys.stderr)
    print(f"channel_kinetics: {len(payload['solve']['channel_kinetics'])}", file=sys.stderr)
    if gap.unstorable_fields:
        print(f"UNSTORABLE (schema gap): {gap.unstorable_fields}", file=sys.stderr)
    else:
        print("UNSTORABLE (schema gap): none", file=sys.stderr)
    if gap.followups:
        print(f"FOLLOW-UPS: {gap.followups}", file=sys.stderr)

    validated_ok = None
    if not args.no_validate:
        try:
            from app.schemas.workflows.network_pdep_upload import (
                NetworkPDepUploadRequest,
            )

            request = NetworkPDepUploadRequest.model_validate(payload)
            payload = request.model_dump(mode="json", exclude_none=True)
            validated_ok = True
            print("VALIDATION: PASS (schema-valid NetworkPDepUploadRequest)", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            validated_ok = False
            print("VALIDATION: FAIL", file=sys.stderr)
            print(repr(exc), file=sys.stderr)

    text = json.dumps(payload, indent=2, sort_keys=False)
    if args.out:
        args.out.write_text(text)
        print(f"wrote payload -> {args.out}", file=sys.stderr)
    else:
        # Only print full payload to stdout when not writing a file, and keep
        # it terse if huge.
        if len(text) < 200_000:
            print(text)
        else:
            print(f"[payload {len(text)} bytes; use --out to write]", file=sys.stderr)

    if validated_ok is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
