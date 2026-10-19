#!/usr/bin/env python3
"""Refuse a change that makes the versioned-names debt list bigger than where it branched.

WHY THIS IS NOT JUST A TEST
    ``backend/tests/test_no_versioned_names.py`` fails on a new violation, on a
    stale debt row, and on a debt list that gains rows or occurrences beyond
    two ceilings. All of that is measured against numbers written in the same
    pull request, so a change that fixes one violation and plants another, and
    swaps the debt row to match, leaves every total unchanged and passes.

    The only reference the author cannot edit is the base branch. This script
    reads ``DEBT`` from the merge base, through git, and requires that every
    row of the head's ``DEBT`` already exists at the merge base with a count at
    least as large. Shrinking is always fine; a new key or a higher count is
    refused.

WHY GIT AND NOT A CHECKED-IN SNAPSHOT
    A snapshot lives in the pull request, so the same edit that swaps the row
    swaps the snapshot. The merge base does not.

WHERE IT RUNS
    ``.github/workflows/repo-gate.yml``, the one job that checks out full
    history (the backend gates use a depth-1 checkout, which has no merge
    base). It uses only the standard library, so that job needs no extra
    install. ``backend/tests/scripts/test_check_versioned_debt_not_grown.py``
    tests the comparison and a real two-commit repository.

If the merge base has no debt file at all, the head is introducing the guard
and there is nothing to be larger than; the script says so and passes.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

DEBT_PATH = "backend/tests/versioned_names_debt.py"
Row = tuple[str, str, str, int, str]


def parse_debt(source: str) -> dict[tuple[str, str, str], int]:
    """``DEBT`` from the source of the debt module, without importing it."""
    for node in ast.parse(source).body:
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "DEBT":
            value = node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "DEBT"
        ):
            value = node.value
        if value is not None:
            rows = ast.literal_eval(value)
            return {(path, kind, token): count for path, kind, token, count, _pr in rows}
    raise ValueError("no DEBT assignment found")


def grown(base: dict[tuple[str, str, str], int], head: dict[tuple[str, str, str], int]) -> list[str]:
    """What the head's debt has that the base's does not allow."""
    problems = []
    for key, count in sorted(head.items()):
        if key not in base:
            problems.append(f"new debt row {key!r} (x{count}); a new violation must be fixed, not listed")
        elif count > base[key]:
            problems.append(f"debt row {key!r} grew from x{base[key]} to x{count}")
    return problems


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base-ref", required=True, help="the pull request's base (a sha or ref)")
    parser.add_argument(
        "--head-ref",
        default=None,
        help="the pull request's head; default is the working-tree file, for local runs",
    )
    args = parser.parse_args(argv)

    merge_base = _git(args.repo, "merge-base", args.base_ref, args.head_ref or "HEAD")
    if merge_base.returncode != 0:
        print(
            f"cannot compute a merge base of {args.base_ref} and {args.head_ref or 'HEAD'} "
            f"(shallow checkout? fetch full history): {merge_base.stderr.strip()}",
            file=sys.stderr,
        )
        return 2
    base_sha = merge_base.stdout.strip()

    base_file = _git(args.repo, "show", f"{base_sha}:{DEBT_PATH}")
    if base_file.returncode != 0:
        print(f"{DEBT_PATH} does not exist at the merge base ({base_sha[:9]}): the guard is being introduced.")
        return 0

    if args.head_ref:
        head_file = _git(args.repo, "show", f"{args.head_ref}:{DEBT_PATH}")
        if head_file.returncode != 0:
            print(f"{DEBT_PATH} is missing at {args.head_ref}: deleted (the programme is finished).")
            return 0
        head_source = head_file.stdout
    else:
        path = Path(args.repo) / DEBT_PATH
        if not path.exists():
            print(f"{DEBT_PATH} is missing in the working tree.")
            return 0
        head_source = path.read_text(encoding="utf-8")

    problems = grown(parse_debt(base_file.stdout), parse_debt(head_source))
    if problems:
        print(f"The versioned-names debt grew relative to the merge base {base_sha[:9]}:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"Versioned-names debt has not grown since the merge base {base_sha[:9]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
