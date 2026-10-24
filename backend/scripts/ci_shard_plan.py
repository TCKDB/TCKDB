#!/usr/bin/env python3
"""Build the duration-balanced shard plan from a real CI run's JUnit files.

    ci_shard_plan.py --write \
        --gate api=junit-api.xml:3 --gate scientific=junit-scientific.xml:2 \
        --gate rest=junit-rest.xml:2

Each ``--gate NAME=JUNIT[,JUNIT...]:SLICES`` sums the recorded time of every
test in each file, then deals the files out longest-first onto the currently
lightest slice (LPT), which is within a few percent of optimal for this
many files. The result is ``backend/tests/ci_shard_plan.json``.

The plan only affects *balance*. Which slice runs a file that is not in the
plan is decided by ``tests/ci_sharding.fallback_slice``, so a stale plan
makes a slice slower, never a test unrun. Regenerate it when the wall clock
of one slice drifts away from the others -- the job times in the Actions UI
are the signal -- not on every change.

Time is the sum of the per-test ``time`` attribute, which includes fixture
setup and teardown, so a file whose cost is in its fixtures is weighed as it
runs. Run it from a JUnit produced the way the gate runs (same worker count);
the absolute numbers do not matter, only their ratios.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PLAN = BACKEND_ROOT / "tests" / "ci_shard_plan.json"


def file_of(classname: str) -> str:
    """``tests.api.test_x.TestY`` -> ``tests/api/test_x.py``."""
    module = []
    for part in classname.split("."):
        if part[:1].isupper():
            break
        module.append(part)
    return "/".join(module) + ".py"


def weigh(junit_paths: list[Path]) -> dict[str, float]:
    weights: dict[str, float] = collections.defaultdict(float)
    for path in junit_paths:
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            weights[file_of(case.get("classname", ""))] += float(case.get("time", 0.0))
    return dict(weights)


def deal(weights: dict[str, float], slices: int) -> tuple[dict[str, int], list[float]]:
    load = [0.0] * slices
    assignment: dict[str, int] = {}
    for path, weight in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        lightest = min(range(slices), key=lambda index: (load[index], index))
        assignment[path] = lightest + 1
        load[lightest] += weight
    return assignment, load


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gate", action="append", required=True, metavar="NAME=JUNIT[,JUNIT]:SLICES")
    parser.add_argument("--write", action="store_true", help=f"write {PLAN.relative_to(BACKEND_ROOT.parent)}")
    args = parser.parse_args()

    plan: dict[str, dict] = {}
    for spec in args.gate:
        name, _, rest = spec.partition("=")
        files, _, slices = rest.rpartition(":")
        weights = weigh([Path(p) for p in files.split(",")])
        assignment, load = deal(weights, int(slices))
        plan[name] = {"slices": int(slices), "files": dict(sorted(assignment.items()))}
        total = sum(load)
        print(
            f"{name}: {len(weights)} files, {total:,.0f}s of test time -> "
            + ", ".join(f"slice {i + 1} {w / total:.1%}" for i, w in enumerate(load))
        )
    document = json.dumps(plan, indent=1, sort_keys=True) + "\n"
    if args.write:
        PLAN.write_text(document, encoding="utf-8")
        print(f"wrote {PLAN}")
    else:
        sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
