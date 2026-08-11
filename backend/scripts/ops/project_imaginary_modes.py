#!/usr/bin/env python
"""Sweep a corpus's imaginary modes through the ADR 0012 projections.

The read API answers one calculation at a time. This answers the question
that decides whether the projections were worth building: across every
record that has an imaginary mode, how many resolve as rigid-body residue,
how many as a torsion, how many as neither -- and, where a depositor
declared a disposition, how often the determination and the declaration
disagree.

**Read-only, and writes nothing anywhere.** It opens a session, runs
``SELECT``s, and prints. There is no table for it to write to; ADR 0013's
objection is precisely that a projection is an inference and TCKDB stores
observations. Re-running it after a parser or threshold change is how the
corpus gets re-decided, which is the property ADR 0013 asks a future
implementation to preserve.

Usage::

    # The whole corpus.
    python backend/scripts/ops/project_imaginary_modes.py --all

    # One calculation, by public ref or integer id.
    python backend/scripts/ops/project_imaginary_modes.py --calculation-ref calc_...

    # Per-record detail rather than the summary table.
    python backend/scripts/ops/project_imaginary_modes.py --all --verbose

    # Accept a scope that is legitimately empty.
    python backend/scripts/ops/project_imaginary_modes.py --all --allow-empty

Exit status separates the two ways this stops being informative, on the
same principle as ``verify_artifact_integrity.py``: ``1`` when at least
one determination contradicts a declaration -- a finding an operator must
look at -- and ``2`` when the scope resolved to nothing, because a
confident summary over an empty set is the defect that script was built to
close. ``0`` means every in-scope record was projected or explicitly
accounted for.

Records with no Hessian are counted as **not determinable**, never as
"no residue found". That distinction is the whole reason the status
vocabulary exists, and collapsing it here would undo it.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.calculation import (  # noqa: E402
    Calculation,
    CalculationFreqMode,
)
from app.services.scientific_read.imaginary_mode_projection import (  # noqa: E402
    DeclarationAgreement,
    ProjectionStatus,
    build_imaginary_mode_projection,
)

EXIT_OK = 0
EXIT_CONFLICT = 1
EXIT_EMPTY_SCOPE = 2


def _scope(session: Session, args) -> list[Calculation]:
    """Resolve the calculations in scope, newest id last."""

    statement = select(Calculation).order_by(Calculation.id)
    if args.calculation_ref is not None:
        handle = args.calculation_ref
        if handle.isdigit():
            statement = statement.where(Calculation.id == int(handle))
        else:
            statement = statement.where(Calculation.public_ref == handle)
    else:
        # Every calculation carrying at least one imaginary mode. A
        # calculation with none is not in scope: there is nothing to
        # project and saying so per record would bury the finding.
        statement = statement.where(
            select(CalculationFreqMode.calculation_id)
            .where(
                CalculationFreqMode.calculation_id == Calculation.id,
                CalculationFreqMode.is_imaginary.is_(True),
            )
            .exists()
        )
    if args.limit is not None:
        statement = statement.limit(args.limit)
    return list(session.scalars(statement).all())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--all",
        action="store_true",
        help="every calculation carrying an imaginary mode",
    )
    scope.add_argument("--calculation-ref", help="one calculation, by public ref or integer id")
    parser.add_argument("--limit", type=int, default=None, help="cap records read")
    parser.add_argument("--verbose", action="store_true", help="print every mode, not just totals")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="accept a scope with no imaginary modes in it",
    )
    args = parser.parse_args()

    from app.api.deps import SessionLocal

    statuses: Counter[str] = Counter()
    determinations: Counter[str] = Counter()
    agreements: Counter[str] = Counter()
    conflicts: list[str] = []

    with SessionLocal() as session:
        targets = _scope(session, args)
        if not targets:
            if args.allow_empty:
                print("RESULT: scope is empty and --allow-empty was given. Nothing was projected.")
                return EXIT_OK
            print(
                "RESULT: NOT PROJECTED. No calculation in scope carries an "
                "imaginary mode, so nothing was measured. That is a scope "
                "error, not a clean result -- pass --allow-empty if the "
                "deployment is genuinely empty."
            )
            return EXIT_EMPTY_SCOPE

        for calc in targets:
            result = build_imaginary_mode_projection(session, calc.id)
            statuses[result.status.value] += 1
            if args.verbose:
                print(
                    f"{calc.public_ref}  natoms={result.natoms}  "
                    f"status={result.status.value}  "
                    f"rotatable={len(result.rotatable_bonds)}"
                )
            for mode in result.modes:
                key = (
                    mode.determination.value
                    if mode.determination is not None
                    else f"not_determined:{mode.not_determined_reason}"
                )
                determinations[key] += 1
                agreements[mode.agreement.value] += 1
                if args.verbose:
                    print(
                        f"    mode {mode.mode_index:>3}  "
                        f"{mode.frequency_cm1:10.2f} cm-1  "
                        f"rb={_fmt(mode.rigid_body_overlap)}  "
                        f"tors={_fmt(mode.torsion_overlap)}  "
                        f"-> {key}  "
                        f"declared={_declared(mode.declared_disposition)}  "
                        f"({mode.agreement.value})"
                    )
                if mode.agreement is DeclarationAgreement.conflicts:
                    conflicts.append(
                        f"{calc.public_ref} mode {mode.mode_index} "
                        f"({mode.frequency_cm1:.1f} cm-1): declared "
                        f"{_declared(mode.declared_disposition)}, determined "
                        f"{key}"
                    )

    print(f"\n{len(targets)} calculation(s) with at least one imaginary mode.\n")
    print("status                            records")
    for status in ProjectionStatus:
        print(f"  {status.value:<32s}{statuses.get(status.value, 0):>5d}")
    not_determinable = statuses.get(ProjectionStatus.hessian_not_stored.value, 0)
    if not_determinable:
        print(
            f"\n  {not_determinable} of these carry no Hessian. Those are not "
            "determinable here -- they are not records where no residue was "
            "found."
        )

    if determinations:
        print("\ndetermination                      modes")
        for key, count in sorted(determinations.items()):
            print(f"  {key:<32s}{count:>5d}")
    if agreements:
        print("\nagainst the declared disposition   modes")
        for key, count in sorted(agreements.items()):
            print(f"  {key:<32s}{count:>5d}")

    if conflicts:
        print(f"\n{len(conflicts)} determination(s) contradict a declaration:")
        for line in conflicts:
            print(f"  {line}")
        print(
            "\nRESULT: CONFLICT. Both readings are reported by the API; "
            "neither is preferred, and nothing has been changed. A conflict "
            "is a curation question, not a validation failure -- under "
            "ADR 0008 a projection is an expectation and may not block."
        )
        return EXIT_CONFLICT

    print("\nRESULT: no determination contradicts a declaration.")
    return EXIT_OK


def _fmt(value: float | None) -> str:
    return "  n/a " if value is None else f"{value:6.4f}"


def _declared(value) -> str:
    return "none" if value is None else value.value


if __name__ == "__main__":
    raise SystemExit(main())
