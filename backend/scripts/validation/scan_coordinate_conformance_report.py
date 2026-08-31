#!/usr/bin/env python
"""Report, per deposited scan series, whether coordinate_value matches its geometry.

ADR 0020 (``docs/adr/0020-a-scan-coordinate-value-is-the-coordinate-itself.md``)
fixes ``calc_scan_point_coordinate_value.coordinate_value`` as the internal
coordinate itself, at that point's own sampled geometry, in that coordinate's
own unit -- never a displacement, never anchored to ``start_value``. This
script runs the read-time conformance check
(:mod:`app.services.scan_coordinate_conformance`) across every scan
calculation reachable in a database and prints, per coordinate series: the
classification, the residual distribution, the implied constant where a
systematic pattern was found, and how many points could not be judged at all.

This is the diagnostic ADR 0020 explicitly asks for **ahead of** the
corrective migration: "the correction of the 46 already-deposited series and
the conformance check that finds non-conforming deposits are decided [in ADR
0020]; ... implemented separately." This script's output is what that later
migration is written from, one series at a time -- it does not write
anything itself, and it does not touch ``start_value``/``end_value`` to
compute an expected value.

**Read-only, and writes nothing anywhere.** Same contract as
``backend/scripts/ops/project_imaginary_modes.py``: a session is opened,
``SELECT``\\ s are run, and the process prints. There is no table for a
conformance verdict to be written to.

Usage::

    # Every scan calculation in the target database.
    conda run -n tckdb_env python backend/scripts/validation/scan_coordinate_conformance_report.py --all

    # One calculation, by public ref or integer id.
    conda run -n tckdb_env python backend/scripts/validation/scan_coordinate_conformance_report.py \\
        --calculation-ref calc_...

    # Per-point detail rather than just the series summary.
    conda run -n tckdb_env python backend/scripts/validation/scan_coordinate_conformance_report.py --all --verbose

    # Accept a scope that is legitimately empty (e.g. a fresh dev DB).
    conda run -n tckdb_env python backend/scripts/validation/scan_coordinate_conformance_report.py --all --allow-empty

Exit status, on the same principle as ``project_imaginary_modes.py``: ``1``
when at least one coordinate series is classified as anything other than
``conforms`` or ``insufficient_data`` -- the finding an operator (or the
follow-up migration) needs to look at -- and ``2`` when the scope resolved to
no scan calculations, because a confident "nothing to report" over an empty
set is exactly the failure mode that script was built to close. ``0`` means
every *checkable* scan coordinate series in scope conforms to ADR 0020.

``insufficient_data`` is deliberately excluded from the exit-1 accounting: it
is not a conformance verdict, it means too few checkable points existed to
say anything at all (an ``improper`` coordinate, correctly ``not_applicable``
at every point, is the common case) -- counting it as "does not conform"
would put a structurally unrelated absence in the same bucket as an actual
non-conforming deposit. It is still printed, in its own section, because
"nothing was measured" and "nothing was found wrong" are different findings.

On the corpus this was built against, every series is expected to exit ``1``:
all 46 deposited scan series predate ADR 0020 and hold ADR 0019's superseded
relative-sweep convention. That is this script doing its job, not a defect in
it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.calculation import Calculation  # noqa: E402
from app.db.models.common import CalculationType  # noqa: E402
from app.services.scan_coordinate_conformance import (  # noqa: E402
    CoordinateSeriesConformance,
    PointStatus,
    SeriesClassification,
    build_scan_coordinate_conformance_report,
)

EXIT_OK = 0
EXIT_NONCONFORMING = 1
EXIT_EMPTY_SCOPE = 2


def _scope(session: Session, args: argparse.Namespace) -> list[Calculation]:
    """Resolve the scan calculations in scope, lowest id first."""

    statement = (
        select(Calculation)
        .where(Calculation.type == CalculationType.scan)
        .order_by(Calculation.id)
    )
    if args.calculation_ref is not None:
        handle = args.calculation_ref
        if handle.isdigit():
            statement = statement.where(Calculation.id == int(handle))
        else:
            statement = statement.where(Calculation.public_ref == handle)
    if args.limit is not None:
        statement = statement.limit(args.limit)
    return list(session.scalars(statement).all())


def _print_series(calc: Calculation, series: CoordinateSeriesConformance, *, verbose: bool) -> None:
    dist = series.residual_distribution
    dist_str = (
        f"min={dist.min:.6g} median={dist.median:.6g} "
        f"p95={dist.p95:.6g} max={dist.max:.6g}"
        if dist is not None
        else "n/a (no checkable points)"
    )
    print(
        f"{calc.public_ref}  coordinate_index={series.coordinate_index}  "
        f"kind={series.kind.value}  classification={series.classification.value}"
    )
    print(f"    residual distribution:  {dist_str}")
    if series.implied_constant is not None:
        print(f"    implied constant:       {series.implied_constant:.6g}")
    print(
        f"    points: {series.n_points}  "
        f"not_applicable={series.n_not_applicable}  "
        f"not_checkable={series.n_not_checkable}"
    )
    print(f"    detail: {series.classification_detail}")
    if verbose:
        for point in series.points:
            if point.status is PointStatus.not_applicable:
                print(f"      point {point.point_index:>4}: not_applicable ({point.reason})")
            elif point.status is PointStatus.not_checkable:
                print(f"      point {point.point_index:>4}: not_checkable ({point.reason})")
            else:
                print(
                    f"      point {point.point_index:>4}: {point.status.value:<9} "
                    f"stored={point.stored_value:.6g}  expected={point.expected_value:.6g}  "
                    f"residual={point.residual:.6g}  tol={point.tolerance:.3g}"
                )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="every scan calculation")
    scope.add_argument(
        "--calculation-ref", help="one scan calculation, by public ref or integer id"
    )
    parser.add_argument("--limit", type=int, default=None, help="cap records read")
    parser.add_argument(
        "--verbose", action="store_true", help="print every point, not just the series summary"
    )
    parser.add_argument(
        "--allow-empty", action="store_true", help="accept a scope with no scan calculations in it"
    )
    args = parser.parse_args()

    from app.api.deps import SessionLocal

    classification_counts: dict[str, int] = {c.value: 0 for c in SeriesClassification}
    nonconforming: list[str] = []
    # insufficient_data is not a conformance verdict at all -- it means the
    # series had too few checkable points to say anything (e.g. an
    # ``improper`` coordinate, correctly reported not_applicable at every
    # point). Counting it as "does not conform to ADR 0020" would put a
    # structurally unrelated absence in the same bucket as an actual
    # non-conforming deposit, so it gets its own, separate accounting.
    uncheckable: list[str] = []

    with SessionLocal() as session:
        targets = _scope(session, args)
        if not targets:
            if args.allow_empty:
                print("RESULT: scope is empty and --allow-empty was given. Nothing was checked.")
                return EXIT_OK
            print(
                "RESULT: NOT CHECKED. No scan calculation is in scope, so nothing "
                "was measured. That is a scope error, not a clean result -- pass "
                "--allow-empty if the deployment is genuinely empty of scans."
            )
            return EXIT_EMPTY_SCOPE

        n_series = 0
        for calc in targets:
            for series in build_scan_coordinate_conformance_report(session, calc.id):
                n_series += 1
                classification_counts[series.classification.value] += 1
                _print_series(calc, series, verbose=args.verbose)
                label = f"{calc.public_ref} coordinate_index={series.coordinate_index}"
                if series.classification is SeriesClassification.insufficient_data:
                    uncheckable.append(f"{label} ({series.classification.value})")
                elif series.classification is not SeriesClassification.conforms:
                    nonconforming.append(f"{label} ({series.classification.value})")

    print(f"{len(targets)} scan calculation(s), {n_series} coordinate series.\n")
    print("classification                              series")
    for classification in SeriesClassification:
        count = classification_counts[classification.value]
        print(f"  {classification.value:<42s}{count:>5d}")

    if uncheckable:
        print(
            f"\n{len(uncheckable)} series could not be checked at all -- not "
            "evidence for or against ADR 0020 conformance:"
        )
        for line in uncheckable:
            print(f"  {line}")

    if nonconforming:
        print(
            f"\n{len(nonconforming)} series do not conform to ADR 0020:"
        )
        for line in nonconforming:
            print(f"  {line}")
        print(
            "\nRESULT: NONCONFORMING. Per ADR 0008 this is a warn-tier finding, "
            "never a refusal -- the deposits above are accepted and unchanged. "
            "The classification and implied constant printed for each series is "
            "the input to the separate corrective migration ADR 0020 defers."
        )
        return EXIT_NONCONFORMING

    print("\nRESULT: every checkable scan coordinate series in scope conforms to ADR 0020.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
