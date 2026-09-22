#!/usr/bin/env python
"""Run one Phase D advisory comparison. Defaults to a read-only dry run."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.consistency.engine import ConfigurationError
from app.services.consistency.service import invoke


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", required=True, choices=("thermo", "external-cp", "thermo-kinetics"))
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--comparison-thermo-ref")
    parser.add_argument("--reverse-kinetics-ref")
    parser.add_argument("--thermo", action="append", default=[], metavar="SPECIES_ENTRY_REF=THERMO_REF")
    parser.add_argument("--temperature", action="append", type=float, default=[])
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args(argv)
    mapping = {}
    for pair in args.thermo:
        if pair.count("=") != 1:
            parser.error("--thermo requires SPECIES_ENTRY_REF=THERMO_REF")
        entry_ref, thermo_ref = pair.split("=")
        if entry_ref in mapping:
            parser.error("each species-entry reference must occur only once")
        mapping[entry_ref] = thermo_ref
    from app.api.deps import SessionLocal
    with SessionLocal() as session:
        try:
            result, _row = invoke(
                session, check=args.check, target_ref=args.target_ref, commit=args.commit,
                comparison_thermo_ref=args.comparison_thermo_ref, reverse_kinetics_ref=args.reverse_kinetics_ref,
                thermo_mapping=mapping, temperature_grid=args.temperature,
            )
            output = {"target_ref": result.target.public_ref, "check": args.check,
                      "committed": args.commit, "context_hash": result.digest.context_hash,
                      "findings": [f.model_dump(mode="json") for f in result.findings]}
            if args.commit:
                session.commit()
            else:
                session.rollback()
            print(json.dumps(output, sort_keys=True))
            return 0
        except (ConfigurationError, ValueError) as exc:
            session.rollback()
            print(str(exc), file=sys.stderr)
            return 2 if isinstance(exc, ConfigurationError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
