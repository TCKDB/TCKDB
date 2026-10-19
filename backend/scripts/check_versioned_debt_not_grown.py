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


class DebtFileError(ValueError):
    """The debt module is not in the plain shape this check can read safely."""


def literal_binding(source: str, name: str) -> tuple[object, ...]:
    """The value of the one top-level ``name = <literal tuple>`` in *source*, strictly.

    Refused (``DebtFileError``): no binding, more than one binding of the name
    anywhere in the module (a second ``DEBT = DEBT + (...)``, an augmented
    assignment, a loop target, an import or a def of the same name all count),
    and a value that is not a plain literal tuple. Reading the first assignment
    and ignoring the rest is how a later ``DEBT = DEBT + ((new row),)`` would
    slip a row past a check that only parsed the first.
    """
    tree = ast.parse(source)
    bindings = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
            bindings += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            bindings += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bindings += sum(1 for alias in node.names if (alias.asname or alias.name.split(".")[0]) == name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            bindings += 1
    if bindings != 1:
        raise DebtFileError(f"{name} must be bound exactly once in the debt module; found {bindings} bindings")
    value: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == name:
                value = node.value
    if value is None:
        raise DebtFileError(f"{name} must be a top-level assignment of a literal tuple")
    if not isinstance(value, ast.Tuple):
        raise DebtFileError(f"{name} must be a plain literal tuple, not {type(value).__name__}")
    parsed = ast.literal_eval(value)
    if not isinstance(parsed, tuple):
        raise DebtFileError(f"{name} must be a plain literal tuple")
    return parsed


def parse_debt(source: str) -> dict[tuple[str, str, str], int]:
    """``DEBT`` from the source of the debt module, without importing it."""
    rows = literal_binding(source, "DEBT")
    result: dict[tuple[str, str, str], int] = {}
    for row in rows:
        if not (isinstance(row, tuple) and len(row) == 5 and isinstance(row[3], int)):
            raise DebtFileError(f"a DEBT row is not (path, kind, token, count, pr): {row!r}")
        path, kind, token, count, _pr = row
        result[(path, kind, token)] = count
    return result


def parse_allowed(source: str) -> dict[tuple[str, str, str], str]:
    """``ALLOWED`` as {(path glob, kind, token glob): reason}."""
    result: dict[tuple[str, str, str], str] = {}
    for row in literal_binding(source, "ALLOWED"):
        if not (isinstance(row, tuple) and len(row) == 4):
            raise DebtFileError(f"an ALLOWED row is not (path, kind, token, reason): {row!r}")
        result[(row[0], row[1], row[2])] = row[3]
    return result


def parse_new_allowances(source: str) -> set[tuple[str, str, str]]:
    """``NEW_ALLOWANCES``: the allowances this change says it adds."""
    return {tuple(row) for row in literal_binding(source, "NEW_ALLOWANCES")}  # type: ignore[misc]


def new_allowances(
    base: dict[tuple[str, str, str], str],
    head: dict[tuple[str, str, str], str],
    declared: set[tuple[str, str, str]],
) -> tuple[list[str], list[str]]:
    """(problems, notices): an ALLOWED entry the base lacks must be declared in NEW_ALLOWANCES.

    A widened glob is a different key, so it counts as new. The declaration is
    a marker, not a permission: the script prints every new entry with its
    reason so the reviewer sees it, and an undeclared one fails.
    """
    problems, notices = [], []
    for key in sorted(set(head) - set(base)):
        if key in declared:
            notices.append(f"NEW ALLOWANCE for the reviewer: {key!r}: {head[key]}")
        else:
            problems.append(f"new allowance {key!r} is not declared in NEW_ALLOWANCES")
    for key in sorted(declared - set(head)):
        problems.append(f"NEW_ALLOWANCES names {key!r}, which is not in ALLOWED")
    return problems, notices


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

    if not args.base_ref.strip("0") or not args.base_ref.strip():
        print(
            "the base ref is empty or all zeros (a push that created a branch has no 'before'). "
            "Failing closed: a check with nothing to compare against must not report success.",
            file=sys.stderr,
        )
        return 2

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

    try:
        base_debt = parse_debt(base_file.stdout)
    except DebtFileError as exc:
        print(f"the merge base's debt file is unreadable ({exc}); fix main first", file=sys.stderr)
        return 2

    if args.head_ref:
        head_file = _git(args.repo, "show", f"{args.head_ref}:{DEBT_PATH}")
        head_source = head_file.stdout if head_file.returncode == 0 else None
    else:
        path = Path(args.repo) / DEBT_PATH
        head_source = path.read_text(encoding="utf-8") if path.exists() else None

    if head_source is None:
        if not base_debt:
            print(f"{DEBT_PATH} is gone and the merge base's DEBT was already empty: the programme is finished.")
            return 0
        print(
            f"{DEBT_PATH} is missing at the head but the merge base still lists {len(base_debt)} debt rows. "
            "Deleting the file is not a way to clear them; fix the violations.",
            file=sys.stderr,
        )
        return 1

    try:
        head_debt = parse_debt(head_source)
        problems = grown(base_debt, head_debt)
        allow_problems, notices = new_allowances(
            parse_allowed(base_file.stdout),
            parse_allowed(head_source),
            parse_new_allowances(head_source),
        )
    except DebtFileError as exc:
        print(f"{DEBT_PATH} at the head is not in the shape this check reads: {exc}", file=sys.stderr)
        return 1
    problems += allow_problems
    for notice in notices:
        print(notice)
    if problems:
        print(f"The versioned-names debt grew relative to the merge base {base_sha[:9]}:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"Versioned-names debt has not grown since the merge base {base_sha[:9]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
