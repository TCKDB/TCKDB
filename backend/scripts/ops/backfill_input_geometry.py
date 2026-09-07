#!/usr/bin/env python
"""Backfill true starting geometries for coarse ``opt`` calculations.

``_INPUT_GEOMETRY_TYPES`` in ``app/services/calculation_resolution.py``
documents why an ``opt`` calculation never got an automatic input-geometry
row: "the true input is the pre-opt xyz ... which the producer does not
currently surface". Where a depositor's own payload *did* declare an
``input_geometries`` entry, the only thing it could contain was the
converged result -- so it canonicalizes to the *same* ``Geometry`` row as
the calculation's output, a deposit artifact rather than a second
observation. Either way (no input row, or one that duplicates the output),
the calculation's own uploaded ``input``/``output_log`` artifacts usually
*do* carry the real starting point.

This script walks ``opt`` calculations, and for each one still in that
state, calls
:func:`app.services.input_geometry_extraction.extract_and_link_input_geometry_from_stored_artifacts`
-- the exact same operation the upload-time hook runs, just fed from
already-stored artifacts read from object storage instead of an in-memory
upload payload. It mints/dedupes the extracted geometry
(:func:`app.services.geometry_resolution.resolve_geometry_payload`, the
same dedup-by-hash entry point the upload path uses) and links it with
``source='extracted_from_artifact'``.

**Read-only against artifact storage; writes only ``calculation_input_geometry``
and (when the extracted geometry is new) ``geometry``/``geometry_atom``.**
Never touches ``calculation_output_geometry`` and never re-runs conformer
basin matching or coverage counting -- see the module docstring of
``app.services.input_geometry_extraction`` for why neither is affected by
this backfill.

**Idempotent by construction.** The per-calculation eligibility check
(absent input, or one identical to the output; zero *or* more than one
output geometry is ``ambiguous_output`` either way -- there is nothing
well-posed to compare a candidate against) is re-evaluated on every run; a
row this script has already fixed no longer satisfies it, so a second run
(``--apply`` or not) finds nothing left to do for that calculation.

**Safe against approved calculations.** A calculation whose evidence is
frozen (``record_review.first_approved_at`` set) is skipped with the
``frozen_after_approval`` outcome rather than attempting a write the
deployed accepted-science-immutability trigger would refuse.

**One bad row never aborts the run.** Every per-calculation call is
wrapped in its own ``try``/``except`` in addition to its own ``SAVEPOINT``
(see the loop in ``main()``): an exception the extraction path did not
itself convert to a typed outcome -- unexpected data, a bug -- is caught,
the savepoint is rolled back, and the row is tallied as
``not_determinable`` so the batch reaches the rest of the corpus rather
than dying on one malformed artifact.

**A dry run can still leave a permanent record of a storage integrity
break.** Reading a candidate artifact that fails its digest/size check
records an ``artifact_integrity_event`` row via
``app.services.artifact_integrity.record_from_error`` -- and that function
commits in **its own**, separate session
(``app.services.artifact_integrity.record_integrity_observation``: "Write
one ``artifact_integrity_event`` in its own transaction"), not this
script's session. That commit happens regardless of ``--apply``/
``--dry-run``, because the break is a fact about the object store this
script observed while reading, not a decision this script is making about
``calculation_input_geometry``. A "dry run" here means "no
``calculation_input_geometry``/``geometry`` write survives"; it does not
mean "nothing in the database changes at all".

**The ``no_artifact`` tally does not distinguish "nothing on file" from
"something on file that could not be read".** A calculation with zero
``input``/``output_log`` artifact rows and a calculation whose only such
rows all failed to read (storage unavailable, or an integrity break)
both land in the same ``no_artifact`` count -- the first is an absence,
the second is itself news (especially for an integrity break, which is
never silent: it is recorded as described above). To tell them apart:
run with ``--verbose``, whose per-calculation line carries the outcome's
``reason`` (present only for the "something failed to read" case, e.g.
"artifacts exist but none could be read from storage"; absent -- just
``no_artifact`` -- for the "nothing on file" case), or query
``artifact_integrity_event`` directly for the authoritative record of any
integrity break this run observed.

Usage::

    # Plan only -- the default. Prints the tally, writes nothing.
    python backend/scripts/ops/backfill_input_geometry.py

    # Same as above, spelled explicitly.
    python backend/scripts/ops/backfill_input_geometry.py --dry-run

    # Write it.
    python backend/scripts/ops/backfill_input_geometry.py --apply

    # --apply against a database whose name is not tckdb_test* (a real,
    # deployed database) refuses unless this is also passed:
    python backend/scripts/ops/backfill_input_geometry.py --apply --i-know-this-is-deployed

No calculation id, geometry id, or any other database primary/foreign key
is printed anywhere in this script's default output -- only counts,
grouped by outcome kind. Pass ``--verbose`` to also print one line per
calculation naming its public ref and outcome (never a raw integer id).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.calculation import Calculation  # noqa: E402
from app.db.models.common import CalculationType  # noqa: E402
from app.services.input_geometry_extraction import (  # noqa: E402
    InputGeometryOutcomeKind,
    extract_and_link_input_geometry_from_stored_artifacts,
)

#: Matches the test-harness's own database-naming convention: the fixture
#: default is ``tckdb_test``, ``DB_TEST_NAME`` overrides the whole name,
#: and CI/dev tooling appends a suffix (``tckdb_test_ci``, ``tckdb_test_ingeom``).
#: Anchored both ends, same shape ``backfill_assumed_tau.py`` uses for the
#: same judgement, so ``tckdb_test-prod`` or ``tckdb_testing_real`` do not
#: pass as a test database by accident.
_TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")


def _scope(session: Session, *, limit: int | None) -> list[Calculation]:
    """Every ``opt`` calculation.

    Deliberately unfiltered beyond ``type='opt'`` -- eligibility (absent
    or degenerate input, frozen-after-approval, ambiguous output) is
    re-checked per calculation by
    :func:`extract_and_link_input_geometry_from_stored_artifacts` itself,
    which is also what the upload hook calls, so the two paths can never
    disagree about what counts as eligible. A calculation already fixed
    by the upload hook, or one with a real distinct input, is inspected
    and tallied as ``already_distinct`` rather than pre-excluded here.
    """
    statement = (
        select(Calculation)
        .where(Calculation.type == CalculationType.opt)
        .order_by(Calculation.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).all())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the resolved input geometries. Without this, the default is a dry run.",
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
        "--limit", type=int, default=None, help="cap the number of opt calculations processed"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also print one line per calculation naming its public ref and outcome",
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
        calculations = _scope(session, limit=args.limit)

        if not calculations:
            print("No 'opt' calculations found. Nothing to do.")
            return 0

        tally: Counter[str] = Counter()
        for calc in calculations:
            # extract_and_link_input_geometry_from_stored_artifacts has no
            # dry-run mode of its own -- it mints/links unconditionally
            # inside its own SAVEPOINTs. A per-calculation SAVEPOINT here
            # is what a dry run rolls back, so a plan-only invocation
            # writes nothing while touching only *this* row's work --
            # never the caller's already-pending session state (e.g. a
            # test fixture's own flushed-but-uncommitted setup, which a
            # session-wide rollback would also discard).
            row_savepoint = session.begin_nested()
            try:
                outcome = extract_and_link_input_geometry_from_stored_artifacts(
                    session, calc
                )
            except Exception as exc:
                # Never let one malformed calculation/artifact abort the
                # rest of the corpus. extract_and_link_input_geometry_from_
                # stored_artifacts converts every expected failure to a
                # typed InputGeometryOutcome already; anything that still
                # raises here is unexpected, and a Postgres SAVEPOINT left
                # in its failed state after an error would poison every
                # later statement in this session if not rolled back --
                # same reasoning as app.services.best_effort.isolated_best_effort.
                row_savepoint.rollback()
                logger.warning(
                    "input_geometry backfill: calculation %s raised "
                    "unexpectedly (%s); recorded as not_determinable and "
                    "the run continues",
                    calc.public_ref,
                    type(exc).__name__,
                    exc_info=exc,
                )
                tally[InputGeometryOutcomeKind.not_determinable.value] += 1
                if args.verbose:
                    print(
                        f"  {calc.public_ref}: not_determinable "
                        f"(unexpected {type(exc).__name__}, see the server log)"
                    )
                continue

            tally[outcome.kind.value] += 1
            if args.verbose:
                detail = f" ({outcome.reason})" if outcome.reason else ""
                print(f"  {calc.public_ref}: {outcome.kind.value}{detail}")
            if args.apply:
                row_savepoint.commit()
            else:
                row_savepoint.rollback()

        if args.apply:
            session.commit()

        verb = "Wrote" if args.apply else "Would write"
        extracted = tally.get(InputGeometryOutcomeKind.extracted.value, 0)
        print(
            f"{verb} an extracted input geometry on {extracted} of "
            f"{len(calculations)} 'opt' calculation(s) examined, by outcome:"
        )
        for outcome_kind, count in sorted(tally.items()):
            print(f"  {outcome_kind}: {count}")
        if not args.apply:
            print("Dry run -- nothing was written. Re-run with --apply to write.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
