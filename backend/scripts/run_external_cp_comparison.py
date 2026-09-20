#!/usr/bin/env python
"""CLI: run the review-tier external-Cp-comparison check and record it.

Thin wrapper over :func:`app.services.external_comparison.cp.run_and_record`
(``docs/research/tckdb-phase-c-implementation-plan.md`` C4). Per ADR 0008
this check runs only from an explicit trigger like this one -- never from the
upload path -- and its only write is one append-only ``record_machine_review``
row: no ``thermo`` or ``molecular_property_observation`` column is touched,
no status implying approval is produced, and no threshold judges the
residuals it reports.

Usage::

    # One thermo record, by public ref (database ids are not accepted).
    python backend/scripts/run_external_cp_comparison.py --thermo-ref thm_...

    # Every computed thermo carrying a NASA-7/NASA-9/point representation
    # with at least one heat_capacity_cp observation on its species entry.
    python backend/scripts/run_external_cp_comparison.py --all

Exit status:

``0``
    Every thermo in scope was compared and recorded.
``1``
    At least one thermo in scope could not be compared (no representation,
    wrong scientific_origin) -- the run continues and reports the rest.
``2``
    Cantera is required and is not installed (configuration error; nothing
    is recorded even for thermo records already visited this run).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db.models.common import ScientificOriginKind  # noqa: E402
from app.db.models.thermo import Thermo, ThermoNASA, ThermoNASA9Interval, ThermoPoint  # noqa: E402
from app.services.external_comparison.cp import (  # noqa: E402
    ExternalCpComparisonConfigurationError,
    run_and_record,
)

EXIT_OK = 0
EXIT_SOME_NOT_COMPARABLE = 1
EXIT_CONFIGURATION_ERROR = 2


def _candidate_thermo_ids(session) -> list[int]:
    """Every computed thermo with a NASA-7, NASA-9, or point representation."""
    nasa7 = set(session.scalars(select(ThermoNASA.thermo_id)))
    nasa9 = set(session.scalars(select(ThermoNASA9Interval.thermo_id)))
    points = set(session.scalars(select(ThermoPoint.thermo_id)))
    candidate_ids = nasa7 | nasa9 | points
    if not candidate_ids:
        return []
    return list(
        session.scalars(
            select(Thermo.id)
            .where(
                Thermo.id.in_(candidate_ids),
                Thermo.scientific_origin == ScientificOriginKind.computed,
            )
            .order_by(Thermo.public_ref)
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--thermo-ref", help="one thermo record, by public ref (thm_...)")
    scope.add_argument("--all", action="store_true", help="every eligible computed thermo record")
    parser.add_argument("--commit", action="store_true", help="persist the recorded row (default: dry-run)")
    args = parser.parse_args()
    if args.thermo_ref is not None and not args.thermo_ref.startswith("thm_"):
        parser.error("--thermo-ref takes a public ref (thm_...); database ids are not an interface")

    from app.api.deps import SessionLocal

    exit_code = EXIT_OK
    with SessionLocal() as session:
        if args.thermo_ref is not None:
            thermo = session.scalar(select(Thermo).where(Thermo.public_ref == args.thermo_ref))
            if thermo is None:
                print(f"no thermo with public ref {args.thermo_ref!r}")
                return EXIT_SOME_NOT_COMPARABLE
            thermo_ids = [thermo.id]
        else:
            thermo_ids = _candidate_thermo_ids(session)
            if not thermo_ids:
                print("RESULT: no eligible computed thermo record in scope.")
                return EXIT_SOME_NOT_COMPARABLE

        recorded = 0
        for thermo_id in thermo_ids:
            try:
                row = run_and_record(session, thermo_id, actor_kind="cli")
            except ExternalCpComparisonConfigurationError as exc:
                print(f"CONFIGURATION ERROR: {exc}")
                session.rollback()
                return EXIT_CONFIGURATION_ERROR
            except ValueError as exc:
                print(f"  skipped: {exc}")
                exit_code = EXIT_SOME_NOT_COMPARABLE
                continue
            print(
                f"  recorded record_machine_review id={row.id} for thermo_id={thermo_id}: "
                f"status={row.status.value}, {len(row.findings_json)} finding(s)"
            )
            recorded += 1

        if args.commit:
            session.commit()
        else:
            session.rollback()
            print("DRY RUN: no row committed (pass --commit to persist).")

    print(f"RESULT: {recorded} of {len(thermo_ids)} thermo record(s) in scope recorded.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
