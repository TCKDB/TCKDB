#!/usr/bin/env python
"""Recover the vibrational spectrum from every stored Hessian and report,
per record, how well it reproduces the deposited frequency list.

The deposited script behind the manuscript's sentence "spectrum recovered
from stored data alone agrees with the separately parsed frequency lists".
It is a thin command-line wrapper over
:mod:`app.services.hessian_reanalysis`, which holds the numerics, the
refusal vocabulary and the derivation of the tolerance; read that module's
docstring for what is compared and why the bound is what it is.

**Read-only.** It opens a session over the configured database
(``DB_*`` environment variables, the same ones ``Settings.database_url``
reads), runs ``SELECT``s, and prints. Nothing is written anywhere, so it
runs unchanged against a restored deposit database.

Usage::

    # Every calculation with a Hessian or a frequency list.
    python backend/scripts/validation/hessian_reanalysis_report.py --all

    # One calculation, by public ref or integer id.
    python backend/scripts/validation/hessian_reanalysis_report.py --calculation-ref calc_...

    # Canonical JSON to a file (byte-identical across runs) and the
    # Markdown table to stdout.
    python backend/scripts/validation/hessian_reanalysis_report.py --all \\
        --json-out reanalysis.json --markdown-out reanalysis.md

    # A tighter or looser bound than the derived default.
    python backend/scripts/validation/hessian_reanalysis_report.py --all --max-deviation-cm1 0.01

Exit status:

``0``
    Every analysable record is within the bound.
``1``
    At least one analysable record has a mode outside the bound. The
    record and the mode are named; the run is still complete.
``2``
    Nothing was analysable -- the scope was empty, or every record in it
    was refused. A report over nothing is not a clean report and there
    is no flag to make it one.

Records without a Hessian are enumerated with status
``hessian_not_stored`` and counted in the denominator. They are not
agreement and they are not dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.hessian_reanalysis import (  # noqa: E402
    DEFAULT_MAX_DEVIATION_CM1,
    DEFAULT_MAX_OMEGA2_DEVIATION_CM2,
    ReanalysisStatus,
    hessian_reanalysis,
)

EXIT_OK = 0
EXIT_EXCEEDED = 1
EXIT_NOTHING_ANALYSABLE = 2


def canonical_json(report: dict) -> str:
    """The one serialisation: sorted keys, fixed indent, trailing newline."""

    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def _fmt(value, decimals: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def markdown_table(report: dict) -> str:
    """One row per record in scope, refusals included."""

    lines = [
        "| calculation | status | N | modes stored/recovered | matched | within bound | max |dev| cm^-1 | rms cm^-1 | max dw^2 cm^-2 | imaginary stored/recovered | declaration conflicts |",
        "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for record in report["records"]:
        conflicts = sum(1 for m in record["imaginary_modes"] if m["agreement"] == "conflicts")
        lines.append(
            "| "
            + " | ".join(
                [
                    record["calculation_ref"] or "-",
                    record["status"],
                    _fmt(record["natoms"]),
                    f"{_fmt(record['stored_count'])}/{_fmt(record['recovered_count'])}",
                    _fmt(record["matched_count"]),
                    (
                        f"{_fmt(record['within_tolerance_count'])}/{_fmt(record['stored_count'])}"
                        if record["status"] == ReanalysisStatus.analysed.value
                        else "-"
                    ),
                    _fmt(record["max_abs_deviation_cm1"]),
                    _fmt(record["rms_abs_deviation_cm1"]),
                    _fmt(record["max_omega2_deviation_cm2"], 3),
                    f"{_fmt(record['stored_imaginary_count'])}/{_fmt(record['recovered_imaginary_count'])}",
                    _fmt(conflicts) if record["status"] == ReanalysisStatus.analysed.value else "-",
                ]
            )
            + " |"
        )
    scope = report["scope"]
    tolerance = report["tolerance"]
    lines.append("")
    lines.append(
        f"{scope['calculation_count']} calculation(s) in scope; {scope['analysed_count']} analysed; "
        f"{scope['within_tolerance_count']} within the bound; {scope['exceeding_count']} exceeding; "
        f"{scope['modes_compared']} modes compared."
    )
    refused = {k: v for k, v in scope["by_status"].items() if k != ReanalysisStatus.analysed.value and v}
    if refused:
        lines.append("Refused: " + ", ".join(f"{k} {v}" for k, v in sorted(refused.items())) + ".")
    lines.append(
        f"Bound: |recovered - stored| <= {tolerance['max_deviation_cm1']} cm^-1 + "
        f"(sqrt(stored^2 + {tolerance['max_omega2_deviation_cm2']} cm^-2) - |stored|)."
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="every calculation with a Hessian or a frequency list")
    scope.add_argument("--calculation-ref", help="one calculation, by public ref or integer id")
    parser.add_argument(
        "--max-deviation-cm1",
        type=float,
        default=DEFAULT_MAX_DEVIATION_CM1,
        help=(
            "flat part of the per-mode bound in cm^-1 (default: the half-ULP of the "
            f"coarsest printed frequency list, {DEFAULT_MAX_DEVIATION_CM1})"
        ),
    )
    parser.add_argument(
        "--max-omega2-deviation-cm2",
        type=float,
        default=DEFAULT_MAX_OMEGA2_DEVIATION_CM2,
        help=(
            "omega-squared part of the per-mode bound in cm^-2 (default: the half-ULP of a "
            f"six-significant-figure force constant, {DEFAULT_MAX_OMEGA2_DEVIATION_CM2:.3f}); "
            "0 makes the bound flat in cm^-1"
        ),
    )
    parser.add_argument("--json-out", type=Path, default=None, help="write the canonical JSON here")
    parser.add_argument("--markdown-out", type=Path, default=None, help="write the Markdown table here")
    parser.add_argument("--no-modes", action="store_true", help="omit the per-mode lists from the JSON")
    parser.add_argument("--quiet", action="store_true", help="do not print the table to stdout")
    args = parser.parse_args()

    from app.api.deps import SessionLocal

    with SessionLocal() as session:
        report = hessian_reanalysis(
            session,
            calculation_ref=None if args.all else args.calculation_ref,
            max_deviation_cm1=args.max_deviation_cm1,
            max_omega2_deviation_cm2=args.max_omega2_deviation_cm2,
            include_modes=not args.no_modes,
        )

    table = markdown_table(report)
    if args.json_out is not None:
        args.json_out.write_text(canonical_json(report))
    if args.markdown_out is not None:
        args.markdown_out.write_text(table)
    if not args.quiet:
        print(table)

    scope = report["scope"]
    if scope["analysed_count"] == 0:
        print(
            "RESULT: NOTHING ANALYSED. "
            + (
                "No calculation in scope carries a Hessian or a frequency list."
                if scope["calculation_count"] == 0
                else f"{scope['calculation_count']} calculation(s) in scope, every one refused (see the status column)."
            )
        )
        return EXIT_NOTHING_ANALYSABLE

    if scope["exceeding_count"]:
        print(f"RESULT: {scope['exceeding_count']} analysed record(s) exceed the bound:")
        for record in report["records"]:
            if record["status"] == ReanalysisStatus.analysed.value and not record["within_tolerance"]:
                worst = None
                for mode in record.get("modes", []):
                    if not mode["within_tolerance"] and (worst is None or abs(mode["deviation_cm1"]) > abs(worst["deviation_cm1"])):
                        worst = mode
                detail = (
                    f" worst mode {worst['mode_index']}: stored {worst['stored_frequency_cm1']:.4f}, "
                    f"recovered {worst['recovered_frequency_cm1']:.4f}, bound {worst['tolerance_cm1']:.4f}"
                    if worst is not None
                    else ""
                )
                print(f"  {record['calculation_ref']}: max |dev| {record['max_abs_deviation_cm1']:.4f} cm^-1;{detail}")
        return EXIT_EXCEEDED

    print(
        f"RESULT: every analysed record ({scope['analysed_count']} of {scope['calculation_count']} in scope) "
        f"is within the bound; max |dev| {scope['max_abs_deviation_cm1']:.4f} cm^-1."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
