#!/usr/bin/env python
"""List stored frequency results whose ADR 0012 decision a new tau would change.

Read-only. This script writes nothing, has no ``--apply``, and never will:
stored ``calc_freq_result`` rows are append-only science. Since the owner
decision of 2026-10-31, an unrecorded ``freq.hessian_method`` resolves
through the producing program's assumed default *at validation*
(``tckdb_schemas.hessian_method_assumption.resolve_tau_with_assumption``),
where before it resolved to the conservative 50 cm-1 and only the stored
value was swapped afterwards. Rows deposited under the old behaviour were
decided at the old tau. This lists them; it does not relabel or re-decide
any of them.

The tau is resolved from the stored ``calculation_parameter`` rows, and those
include the rows the workflow parsed out of an attached input deck. Since
upload validation reads an inline deck before judging (a method stated there
beats an assumed one), this is the same evidence validation uses today, so a
stored row is listed only when today's validation really would decide it
differently. The ORCA release gate reads the stored software release's version.

For every judged row (``imaginary_mode_tau_cm1 IS NOT NULL``) the tau is
re-resolved exactly as upload validation would resolve it today, from that
calculation's own stored ``calculation_parameter`` rows, software and
level-of-theory method. Each row falls into one of:

``minimum would now be refused``
    A species entry of kind ``minimum`` stored with an imaginary mode and a
    structural flag of ``false`` (the shape ``minimum_imaginary_modes_below_tau``
    leaves), whose largest imaginary magnitude is at or above the newly
    resolved tau. Upload would now refuse it with
    ``n_imag_contradicts_minimum``.

``transition-state flag would flip``
    A row with a designated reaction coordinate whose stored structural flag
    differs from the flag the transition-state rule produces at the newly
    resolved tau, judged on the stored mode list. Warn tier only: a flag, never
    a refusal.

``tau differs, decision unchanged``
    The stored tau or basis differs from the new resolution but neither
    decision above changes. Counted only, not listed.

Rows that cannot be re-judged (no stored mode list where one is needed) are
counted as ``unchanged`` by the rule, never guessed at.

Usage::

    python backend/scripts/ops/list_tau_redecided.py

Output carries calculation public refs only; no database primary or foreign
key is printed anywhere.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from tckdb_schemas.hessian_method_assumption import resolve_tau_with_assumption  # noqa: E402
from tckdb_schemas.stationary_point import (  # noqa: E402
    TAU_PARAMETER_KEYS,
    ImaginaryMode,
    StationaryPointKind,
    ValidationTier,
    evaluate_transition_state_frequency,
    has_structural_flag,
)

from app.db.models.calculation import (  # noqa: E402
    Calculation,
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationParameter,
)

MINIMUM_REFUSED = "minimum_would_now_be_refused"
TS_FLAG_FLIPS = "transition_state_flag_would_flip"
UNCHANGED = "tau_differs_decision_unchanged"


@dataclass(frozen=True)
class Redecided:
    """One listed row. ``calculation_ref`` is a public ref, never a row id."""

    category: str
    calculation_ref: str
    largest_imaginary_cm1: float | None
    stored_tau_cm1: float
    stored_basis: str | None
    new_tau_cm1: float
    new_basis: str
    stored_flag: bool | None
    new_flag: bool | None


def _new_tau(session: Session, calc: Calculation):
    recorded = session.execute(
        select(CalculationParameter.canonical_key, CalculationParameter.canonical_value)
        .where(CalculationParameter.calculation_id == calc.id)
        .where(CalculationParameter.canonical_key.in_(TAU_PARAMETER_KEYS))
        .order_by(CalculationParameter.id)
    ).all()
    software = calc.software_release.software.name if calc.software_release is not None else None
    version = calc.software_release.version if calc.software_release is not None else None
    method = calc.lot.method if calc.lot is not None else None
    return resolve_tau_with_assumption(recorded, software, method, version)


def classify(session: Session) -> tuple[list[Redecided], Counter[str], int]:
    """Re-resolve every judged row. Returns (listed rows, tally, rows examined)."""
    listed: list[Redecided] = []
    tally: Counter[str] = Counter()
    rows = session.scalars(
        select(CalculationFreqResult)
        .where(CalculationFreqResult.imaginary_mode_tau_cm1.is_not(None))
        .order_by(CalculationFreqResult.calculation_id)
    ).all()
    for row in rows:
        calc = session.get(Calculation, row.calculation_id)
        if calc is None:
            continue
        new = _new_tau(session, calc)
        same_resolution = (
            new.tau_cm1 == row.imaginary_mode_tau_cm1 and new.basis.value == row.imaginary_mode_tau_basis
        )

        imaginary = [
            ImaginaryMode(
                frequency_cm1=mode.frequency_cm1,
                mode_index=mode.mode_index,
                disposition=mode.imaginary_disposition,
            )
            for mode in session.scalars(
                select(CalculationFreqMode)
                .where(CalculationFreqMode.calculation_id == calc.id)
                .where(CalculationFreqMode.is_imaginary.is_(True))
                .order_by(CalculationFreqMode.mode_index)
            ).all()
        ]
        if imaginary:
            largest: float | None = max(m.magnitude_cm1 for m in imaginary)
        elif row.imag_freq_cm1 is not None:
            largest = abs(row.imag_freq_cm1)
        else:
            largest = None

        category: str | None = None
        new_flag: bool | None = None

        entry = calc.species_entry
        is_minimum = (
            entry is not None
            and entry.kind.value == StationaryPointKind.minimum.value
            and row.reaction_coordinate_mode_index is None
            and (row.n_imag or 0) > 0
            and row.imaginary_mode_structural_flag is False
        )
        if is_minimum and largest is not None and largest >= new.tau_cm1:
            category = MINIMUM_REFUSED
        elif row.reaction_coordinate_mode_index is not None and imaginary:
            findings = evaluate_transition_state_frequency(
                row.n_imag,
                row.imag_freq_cm1,
                location="freq_result",
                imaginary_modes=imaginary,
                reaction_coordinate_mode_index=row.reaction_coordinate_mode_index,
                tau=new,
            )
            if not any(f.tier is ValidationTier.block for f in findings):
                new_flag = has_structural_flag(findings)
                if new_flag != row.imaginary_mode_structural_flag:
                    category = TS_FLAG_FLIPS

        if category is None:
            if not same_resolution:
                tally[UNCHANGED] += 1
            continue
        tally[category] += 1
        listed.append(
            Redecided(
                category=category,
                calculation_ref=calc.public_ref,
                largest_imaginary_cm1=largest,
                stored_tau_cm1=row.imaginary_mode_tau_cm1,
                stored_basis=row.imaginary_mode_tau_basis,
                new_tau_cm1=new.tau_cm1,
                new_basis=new.basis.value,
                stored_flag=row.imaginary_mode_structural_flag,
                new_flag=new_flag,
            )
        )
    return listed, tally, len(rows)


def render(listed: list[Redecided], tally: Counter[str], examined: int) -> str:
    lines = [f"Judged calc_freq_result rows examined: {examined}"]
    for category in (MINIMUM_REFUSED, TS_FLAG_FLIPS):
        rows = [r for r in listed if r.category == category]
        lines.append(f"{category}: {len(rows)}")
        for r in rows:
            largest = "n/a" if r.largest_imaginary_cm1 is None else f"{r.largest_imaginary_cm1:.1f}"
            flags = ""
            if category == TS_FLAG_FLIPS:
                flags = f" flag {r.stored_flag} -> {r.new_flag}"
            lines.append(
                f"  {r.calculation_ref}  largest |nu| {largest} cm-1  "
                f"tau {r.stored_tau_cm1:g} ({r.stored_basis}) -> {r.new_tau_cm1:g} ({r.new_basis}){flags}"
            )
    lines.append(f"{UNCHANGED} (count only): {tally[UNCHANGED]}")
    lines.append("Read-only: nothing was written or relabelled.")
    return "\n".join(lines)


def main() -> int:
    from app.api.deps import SessionLocal

    with SessionLocal() as session:
        # Belt and braces: the transaction refuses any write, so a future edit
        # to this script cannot relabel a stored row by accident.
        session.execute(text("SET LOCAL transaction_read_only = on"))
        listed, tally, examined = classify(session)
        print(render(listed, tally, examined))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
