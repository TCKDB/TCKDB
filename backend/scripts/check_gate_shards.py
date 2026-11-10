#!/usr/bin/env python3
"""Check that the slices of each sharded backend gate add up to the gate.

    check_gate_shards.py REPORT_DIR

``REPORT_DIR`` holds the ``shard-*.json`` files each slice wrote
(``TCKDB_TEST_SHARD_REPORT``, see ``tests/conftest.py``). A gate run as ``n``
slices is only the gate if all of this holds, and each line below is a
separate way for a slice to go missing without any job going red:

1. every gate in ``tests/ci_shard_plan.json`` is present, with exactly the
   slices ``1..n`` the plan was built for -- a leg dropped from the workflow
   matrix, or a report that was never uploaded, is named here;
2. every slice saw the same number of collected tests (``total``) -- slices
   that collected different sets cannot partition anything;
3. the slices' ``selected`` counts add up to that total -- no test is in two
   slices and none is in zero;
4. every selected test reported an outcome (``ran``) -- a slice that exited
   early did not run its share -- and no slice is empty, failed, or had a
   collection error (a test file that cannot be imported contributes no
   tests, so it would otherwise shrink the gate unnoticed);
5. the DR-0028 error-body sweep, summed over the slices, reaches the gate's
   floor (``BODY_SWEEP_FLOORS`` in ``tests/conftest.py``). The floor cannot
   be held per slice -- a slice sees a fraction of the bodies -- so it is held
   here, to the sum, which is exactly the number an unsharded gate gets.

Standard library only; it runs in a job with no environment.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PLAN = BACKEND_ROOT / "tests" / "ci_shard_plan.json"
CONFTEST = BACKEND_ROOT / "tests" / "conftest.py"


def floors() -> dict[tuple[str, ...], tuple[int, int]]:
    """``BODY_SWEEP_FLOORS`` read from conftest.py without importing it."""
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(target, ast.Name) and target.id == "BODY_SWEEP_FLOORS" and node.value is not None:
            return ast.literal_eval(node.value)
    raise SystemExit("BODY_SWEEP_FLOORS not found in tests/conftest.py; this check cannot read the floors")


def check(reports: list[dict], plan: dict, floor_table: dict) -> tuple[list[str], list[str]]:
    """``(problems, summary_lines)``; empty problems means the gates add up."""
    problems: list[str] = []
    summary: list[str] = []
    by_gate: dict[str, list[dict]] = {}
    for report in reports:
        by_gate.setdefault(report["gate"], []).append(report)

    for gate in sorted(set(plan) | set(by_gate)):
        if gate not in plan:
            problems.append(f"{gate}: reports exist for a gate that is not in ci_shard_plan.json")
            continue
        expected = plan[gate]["slices"]
        slices = by_gate.get(gate, [])
        indices = sorted(report["index"] for report in slices)
        if indices != list(range(1, expected + 1)):
            problems.append(f"{gate}: expected slices 1..{expected}, found {indices or 'none'}")
            continue
        if {report["count"] for report in slices} != {expected}:
            problems.append(f"{gate}: slices disagree about the slice count (plan says {expected})")
            continue
        totals = {report["total"] for report in slices}
        if None in totals or len(totals) != 1:
            problems.append(f"{gate}: slices collected different test sets: totals {sorted(map(str, totals))}")
            continue
        total = totals.pop()
        selected = sum(report["selected"] for report in slices)
        ran = sum(report["ran"] for report in slices)
        empty = [report["index"] for report in slices if not report["selected"]]
        if empty:
            problems.append(f"{gate}: slice(s) {empty} selected no tests, so they checked nothing")
        broken = {report["index"]: report.get("collection_errors") for report in slices if report.get("collection_errors") != 0}
        if broken:
            problems.append(
                f"{gate}: collection errors (or a report without the count) in slice(s) {broken}; a test file that "
                "cannot be imported contributes no tests and would shrink the gate"
            )
        failed = {report["index"]: report.get("failed") for report in slices if report.get("failed") != 0}
        if failed:
            problems.append(f"{gate}: failed or errored tests in slice(s) {failed}")
        if selected != total:
            problems.append(f"{gate}: slices selected {selected} tests between them but the gate collects {total}")
        if ran != selected:
            problems.append(
                f"{gate}: {selected} tests selected but {ran} reported an outcome "
                f"({'some never ran' if ran < selected else 'more outcomes than tests selected'})"
            )
        keys = {tuple(report["selection_key"] or ()) for report in slices}
        bodies = sum(report["body_sweep"]["bodies"] for report in slices)
        fields = sum(report["body_sweep"]["fields"] for report in slices)
        if len(keys) != 1:
            problems.append(f"{gate}: slices ran different selections: {sorted(keys)}")
            continue
        floor = floor_table.get(keys.pop())
        if floor is None:
            problems.append(f"{gate}: no BODY_SWEEP_FLOORS row matches this gate's selection, so it has no floor")
        elif bodies < floor[0] or fields < floor[1]:
            problems.append(
                f"{gate}: the DR-0028 body sweep fell below its floor: saw {bodies} bodies / {fields} fields, "
                f"needs {floor[0]} / {floor[1]}"
            )
        per_slice = ", ".join(f"{report['index']}: {report['selected']}" for report in sorted(slices, key=lambda r: r["index"]))
        summary.append(
            f"{gate}: {total} tests = {selected} selected over {expected} slices ({per_slice}); {ran} ran; "
            f"sweep {bodies}/{fields}" + (f" (floor {floor[0]}/{floor[1]})" if floor else "")
        )
    return problems, summary


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    paths = sorted(Path(argv[1]).rglob("shard-*.json"))
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    problems, summary = check(reports, json.loads(PLAN.read_text(encoding="utf-8")), floors())
    text = "\n".join(summary)
    print(f"{len(reports)} slice report(s)\n{text}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write("### Backend gate slices\n\n" + "\n".join(f"- {line}" for line in summary) + "\n")
    if problems:
        print("\nTHE SLICES DO NOT ADD UP TO THE GATES:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
