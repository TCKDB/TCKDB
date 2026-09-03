#!/usr/bin/env python
"""Backfill ADR 0012 tau on ``calc_freq_result`` rows that never got one.

``c2f7a4e8d1b6`` added ``imaginary_mode_tau_cm1`` /
``imaginary_mode_tau_basis`` nullable and backfilled nothing: every
``calc_freq_result`` row deposited before ADR 0012 shipped carries
``imaginary_mode_tau_cm1 IS NULL``, meaning "never judged under ADR
0012" rather than "judged and clean". This script judges them, using
exactly the same resolution the upload path uses today:

1. Read this calculation's own recorded ``calculation_parameter`` rows
   for the three keys :data:`tckdb_schemas.stationary_point.TAU_PARAMETER_KEYS`
   names, and resolve tau from them via
   :func:`tckdb_schemas.stationary_point.resolve_tau_from_parameters` --
   the same function :meth:`CalculationWithResultsPayload.tau_resolution`
   calls at upload time, just fed from stored parameter rows instead of
   an upload payload.
2. If that resolves to the conservative ``protocol_not_recorded`` row
   (the common case -- see the module docstring of
   ``app/services/hessian_method_inference.py`` for the 0-of-132
   measurement that motivated this), try the ADR 0012 2026-09-04
   amendment's assumption table
   (:func:`app.services.hessian_method_inference.infer_hessian_method`)
   against this calculation's software and level-of-theory method. Used
   only when it returns something; ``None`` leaves the row at
   ``protocol_not_recorded``, exactly as the upload path would.

Only ``imaginary_mode_tau_cm1`` and ``imaginary_mode_tau_basis`` are
written. **``imaginary_mode_structural_flag`` is never touched** -- ADR
0012 judges it only where a reaction-coordinate mode was designated
(``reaction_coordinate_mode_index``), and no row this script's scope
reaches has one: they predate the column that carries it. Filling tau
on those rows answers "how tightly can a small imaginary mode be
trusted", not "is this record a genuine higher-order saddle", and this
script only ever answers the first question.

**Idempotent by construction.** The scope is
``imaginary_mode_tau_cm1 IS NULL``; every row this script writes to
leaves that predicate false, so a second run (``--apply`` or not) finds
nothing left to do rather than re-deciding an already-judged row.

Usage::

    # Plan only -- the default. Prints the tally, writes nothing.
    python backend/scripts/ops/backfill_assumed_tau.py

    # Same as above, spelled explicitly.
    python backend/scripts/ops/backfill_assumed_tau.py --dry-run

    # Write it.
    python backend/scripts/ops/backfill_assumed_tau.py --apply

    # --apply against a database whose name is not tckdb_test* (a real,
    # deployed database) refuses unless this is also passed:
    python backend/scripts/ops/backfill_assumed_tau.py --apply --i-know-this-is-deployed

No calculation id, ``calc_freq_result`` row id, or any other database
primary/foreign key is printed anywhere in this script's output -- only
counts, grouped by the tau basis assigned.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from tckdb_schemas.stationary_point import (  # noqa: E402
    TAU_PARAMETER_KEYS,
    TauBasis,
    TauResolution,
    resolve_tau_from_parameters,
)

from app.db.models.calculation import (  # noqa: E402
    Calculation,
    CalculationFreqResult,
    CalculationParameter,
)
from app.services.hessian_method_inference import infer_hessian_method  # noqa: E402

#: Matches the test-harness's own database-naming convention: the fixture
#: default is ``tckdb_test``, ``DB_TEST_NAME`` overrides the whole name,
#: and CI/dev tooling appends a suffix (``tckdb_test_ci``,
#: ``tckdb_test_tau``). Anchored both ends, same shape
#: ``scripts/dev/reclaim_leaked_test_databases.py`` uses for the same
#: judgement, so ``tckdb_test-prod`` or ``tckdb_testing_real`` do not
#: pass as a test database by accident.
_TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")


def _resolve_row_tau(session: Session, calc: Calculation) -> TauResolution:
    """Resolve tau for one calculation exactly as the upload path would.

    :param session: Open session (used only to read ``calculation_parameter``
        rows; ``calc.software_release`` / ``calc.lot`` are read via the
        ORM relationship in the same session).
    :param calc: The owning calculation.
    :returns: Recorded resolution, or the ADR 0012 amendment's assumed
        one when nothing was recorded and the software/method pair is
        in :func:`infer_hessian_method`'s table.
    """
    recorded: dict[str, str | None] = {}
    for key, value in session.execute(
        select(CalculationParameter.canonical_key, CalculationParameter.canonical_value)
        .where(CalculationParameter.calculation_id == calc.id)
        .where(CalculationParameter.canonical_key.in_(TAU_PARAMETER_KEYS))
    ).all():
        recorded[key] = value

    tau = resolve_tau_from_parameters(recorded.items())
    if tau.basis is not TauBasis.protocol_not_recorded:
        return tau

    software_name = (
        calc.software_release.software.name
        if calc.software_release is not None
        else None
    )
    lot_method = calc.lot.method if calc.lot is not None else None
    assumed = infer_hessian_method(software_name, lot_method)
    return assumed if assumed is not None else tau


def _scope(session: Session, *, limit: int | None) -> list[CalculationFreqResult]:
    statement = (
        select(CalculationFreqResult)
        .where(CalculationFreqResult.imaginary_mode_tau_cm1.is_(None))
        .order_by(CalculationFreqResult.calculation_id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).all())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the resolved tau. Without this, the default is a dry run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan without writing (the default; accepted explicitly too)",
    )
    parser.add_argument(
        "--i-know-this-is-deployed",
        action="store_true",
        help=(
            "required alongside --apply to run against a database whose "
            "name does not match tckdb_test*"
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="cap the number of rows processed"
    )
    args = parser.parse_args()

    if args.apply and args.dry_run:
        print("--apply and --dry-run are mutually exclusive.", file=sys.stderr)
        return 2

    from app.api.config import settings
    from app.api.deps import SessionLocal

    db_name = settings.db_name
    is_test_db = bool(_TEST_DB_NAME.match(db_name))
    if args.apply and not is_test_db and not args.i_know_this_is_deployed:
        print(
            "Refusing --apply: the configured database does not match "
            f"{_TEST_DB_NAME.pattern!r} (a test database). If this is "
            "intentionally a deployed database, pass "
            "--i-know-this-is-deployed as well.",
            file=sys.stderr,
        )
        return 2

    with SessionLocal() as session:
        rows = _scope(session, limit=args.limit)

        if not rows:
            print(
                "No calc_freq_result rows have imaginary_mode_tau_cm1 IS "
                "NULL. Nothing to do."
            )
            return 0

        tally: Counter[str] = Counter()
        for row in rows:
            calc = session.get(Calculation, row.calculation_id)
            if calc is None:
                # The FK is enforced under normal replication settings,
                # so this should not happen; skip rather than crash the
                # whole run over one orphaned row.
                continue
            tau = _resolve_row_tau(session, calc)
            tally[tau.basis.value] += 1
            if args.apply:
                row.imaginary_mode_tau_cm1 = tau.tau_cm1
                row.imaginary_mode_tau_basis = tau.basis.value
                # imaginary_mode_structural_flag: never written here --
                # see the module docstring.

        if args.apply:
            session.commit()
        # Dry run: nothing was mutated above (the write only happens inside
        # `if args.apply:`), so there is nothing to roll back -- an
        # explicit rollback() here would roll back whatever transaction is
        # already open on this session, which is not this script's to
        # discard. The session's own context-manager exit closes cleanly
        # without committing anything this run did not commit itself.

        verb = "Wrote" if args.apply else "Would write"
        print(f"{verb} tau on {sum(tally.values())} calc_freq_result row(s), by basis:")
        for basis, count in sorted(tally.items()):
            print(f"  {basis}: {count}")
        if not args.apply:
            print("Dry run -- nothing was written. Re-run with --apply to write.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
