"""Split one CI gate across several runners, by test file, provably.

Why
---
Each backend gate used to be one job running one pytest process on a 4-core
runner for 20-35 minutes, so a pull request waited for the slowest gate. A
gate can be run as ``N`` jobs, each taking a slice of the gate's test
*files*; the wall clock becomes the slowest slice.

How it is selected
------------------
``TCKDB_TEST_SHARD=<gate>:<k>/<n>`` (for example ``api:2/3``) makes a pytest
session keep only the test files assigned to slice ``k`` of ``n`` for that
gate and deselect the rest. Unset, nothing is deselected, which is every
local run and the nightly full-suite job. The workflow sets it from its job
matrix; the gate scripts and their arguments are unchanged, so what a gate
*selects* is still exactly what ``tests/scripts/test_gate_coverage.py``
reads out of the scripts.

Why it cannot drop a test
-------------------------
The assignment is a pure function of the file's path, :func:`shard_of`:

* a file listed in the committed plan (``ci_shard_plan.json``) goes to the
  slice the plan gives it -- the plan is a duration-balanced assignment
  produced by ``scripts/ci_shard_plan.py`` from a real run;
* any other file -- a test file added after the plan was made -- goes to
  ``crc32(path) % n + 1``. It needs no plan entry, so a new file is run the
  day it exists; the plan only affects *balance*, never *coverage*.

Because the function does not depend on which other files were collected, a
file that collects no items cannot shift anyone else's slice, and the
repository-level check can evaluate it over files on disk without
importing pytest or the application.

Three independent checks keep this honest:

1. ``tests/scripts/test_gate_coverage.py`` -- the matrix of
   ``backend-ci.yml`` must contain exactly slices ``1..n`` of each gate, the
   plan must have been built for that ``n``, and every file a gate selects
   must land in a slice in ``1..n``.
2. Every slice reports what it collected and ran
   (``TCKDB_TEST_SHARD_REPORT``). The ``Backend CI`` job sums the reports
   and refuses unless every slice saw the same total, the slices' selected
   counts add up to that total, and the DR-0028 body-sweep floor holds on the
   sum -- the per-slice floor cannot, because a slice sees a fraction.
3. A slice refuses to start if its own selection is empty, so a slice that
   matches nothing is a failure and not a quiet green job.

This module imports nothing from the application and nothing outside the
standard library: the repository-level gate loads it with ``--noconftest``.
"""

from __future__ import annotations

import json
import os
import re
import zlib
from pathlib import Path
from typing import NamedTuple

#: ``api:2/3``. The gate name is checked against the plan, not against a
#: list written here, so a fourth gate needs a plan entry and nothing else.
_SPEC = re.compile(r"^(?P<gate>[a-z][a-z0-9_-]*):(?P<index>[1-9][0-9]*)/(?P<count>[1-9][0-9]*)$")

PLAN_PATH = Path(__file__).with_name("ci_shard_plan.json")


class ShardError(ValueError):
    """The shard spec or the plan is unusable. Never swallowed: a gate that
    cannot say which slice it is must fail, not run everything or nothing."""


class Shard(NamedTuple):
    gate: str
    index: int  # 1-based
    count: int

    def __str__(self) -> str:
        return f"{self.gate}:{self.index}/{self.count}"


def parse(spec: str) -> Shard:
    match = _SPEC.match(spec.strip())
    if match is None:
        raise ShardError(f"TCKDB_TEST_SHARD={spec!r} is not <gate>:<k>/<n> (for example api:2/3)")
    shard = Shard(match["gate"], int(match["index"]), int(match["count"]))
    if shard.index > shard.count:
        raise ShardError(f"TCKDB_TEST_SHARD={spec!r}: slice {shard.index} is past the last slice {shard.count}")
    return shard


def from_environment(environ: dict[str, str] | None = None) -> Shard | None:
    spec = (os.environ if environ is None else environ).get("TCKDB_TEST_SHARD", "").strip()
    return parse(spec) if spec else None


def load_plan(path: Path = PLAN_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fallback_slice(path: str, count: int) -> int:
    """1-based slice for a file the plan has never heard of."""
    return zlib.crc32(path.encode("utf-8")) % count + 1


def shard_of(path: str, gate: str, count: int, plan: dict) -> int:
    """The 1-based slice (of ``count``) that runs ``path`` in ``gate``.

    ``path`` is the pytest node id's file part, relative to ``backend/``
    (``tests/api/test_x.py``). Raises if the plan was built for another
    ``count``: its slices would not be ``1..count``, and silently using them
    would leave some slice with no files.
    """
    entry = plan.get(gate)
    if entry is None:
        raise ShardError(f"the shard plan has no gate {gate!r}; known: {sorted(plan)}")
    if entry["slices"] != count:
        raise ShardError(
            f"the shard plan for {gate!r} was built for {entry['slices']} slices but the workflow runs {count}; "
            "regenerate it with backend/scripts/ci_shard_plan.py --write"
        )
    planned = entry["files"].get(path)
    if planned is not None:
        if not 1 <= planned <= count:
            raise ShardError(f"the shard plan puts {path} in slice {planned} of {count}")
        return planned
    return fallback_slice(path, count)


def file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def partition(paths: list[str], gate: str, count: int, plan: dict) -> dict[int, list[str]]:
    """Every path in exactly one slice ``1..count``; used by the repo gate."""
    slices: dict[int, list[str]] = {index: [] for index in range(1, count + 1)}
    for path in paths:
        slices[shard_of(path, gate, count, plan)].append(path)
    return slices


def select(items: list, shard: Shard, plan: dict) -> tuple[list, list]:
    """``(kept, deselected)`` for one pytest session's collected items."""
    kept: list = []
    dropped: list = []
    for item in items:
        if shard_of(file_of(item.nodeid), shard.gate, shard.count, plan) == shard.index:
            kept.append(item)
        else:
            dropped.append(item)
    return kept, dropped
