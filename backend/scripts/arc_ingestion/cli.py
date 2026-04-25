"""CLI entrypoint for ARC → TCKDB ingestion.

Usage:
    conda run -n tckdb_env python -m scripts.arc_ingestion.cli /path/to/arc/run

Reads a completed ARC run directory and produces a JSON payload compatible
with ComputedReactionUploadRequest.  The payload is written to stdout
(or an optional output file) and can be submitted to the TCKDB API or
validated offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Convert a finished ARC run into a TCKDB upload payload."
    )
    parser.add_argument(
        "arc_dir",
        type=Path,
        help="Path to the ARC run directory (must contain restart.yml).",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Write JSON payload to this file instead of stdout.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the payload against ComputedReactionUploadRequest schema.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print the JSON output (default: True).",
    )

    args = parser.parse_args(argv)

    arc_dir: Path = args.arc_dir.resolve()
    if not arc_dir.is_dir():
        print(f"Error: {arc_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)
    if not (arc_dir / "restart.yml").exists():
        print(f"Error: No restart.yml found in {arc_dir}.", file=sys.stderr)
        sys.exit(1)

    # Import here to keep the CLI lightweight for --help
    from .extractor import ARCRunExtractor
    from .builder import build_payload

    print(f"Extracting ARC run from: {arc_dir}", file=sys.stderr)
    extractor = ARCRunExtractor(arc_dir)
    run_data = extractor.extract()

    print(f"  Project: {run_data.project}", file=sys.stderr)
    print(f"  ARC version: {run_data.arc_version}", file=sys.stderr)
    print(f"  Software: {run_data.software_name} {run_data.software_version} rev {run_data.software_revision}", file=sys.stderr)
    print(f"  Opt LOT: {run_data.opt_level.method}/{run_data.opt_level.basis}", file=sys.stderr)
    print(f"  SP same as opt: {run_data.sp_is_same_as_opt}", file=sys.stderr)
    print(f"  Species: {len(run_data.species)}", file=sys.stderr)
    print(f"  Transition states: {len(run_data.transition_states)}", file=sys.stderr)
    print(f"  Reactions: {len(run_data.reactions)}", file=sys.stderr)

    for label, sp in run_data.species.items():
        status = "converged" if sp.converged else "FAILED"
        print(f"    {label}: {status}", file=sys.stderr)

    for label, ts in run_data.transition_states.items():
        status = "converged" if ts.converged else "FAILED"
        print(f"    {label}: {status}", file=sys.stderr)

    print("\nBuilding payload...", file=sys.stderr)
    payload = build_payload(run_data, arc_dir)

    if args.validate:
        print("Validating against schema...", file=sys.stderr)
        try:
            from app.schemas.workflows.computed_reaction_upload import (
                ComputedReactionUploadRequest,
            )
        except ImportError:
            print(
                "  Skipped: TCKDB app modules not available. "
                "Install TCKDB to use --validate.",
                file=sys.stderr,
            )
        else:
            try:
                ComputedReactionUploadRequest.model_validate(payload)
                print("  Validation PASSED", file=sys.stderr)
            except Exception as e:
                print(f"  Validation FAILED: {e}", file=sys.stderr)
                sys.exit(1)

    indent = 2 if args.pretty else None
    json_str = json.dumps(payload, indent=indent, default=str)

    if args.output:
        args.output.write_text(json_str)
        print(f"\nPayload written to: {args.output}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
