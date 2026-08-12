"""Report the outcome of the path-filtered CI gates as one always-present check.

WHY THIS EXISTS
---------------
A required status check that is path-filtered never reports on a pull
request outside its paths, and GitHub blocks the merge indefinitely
waiting for it. Every test workflow in this repository is path-filtered
-- correctly, because the backend suite costs about thirteen minutes of
runner time and a change to ``paper/`` cannot break it. So none of them
can be listed in branch protection, and until this script existed the
only required check was the coverage guard in ``repo-gate.yml``: the one
workflow with no ``paths:`` filter.

The result was a repository whose test suites gated nothing. They ran,
they went red, and a merge was never blocked by one.

This script is run by a job in ``repo-gate.yml`` -- which always runs --
and reports, as a single check, whether the filtered gates passed. It has
to distinguish three states:

  1. the gates ran and passed                     -> success
  2. the gates ran and failed                     -> failure
  3. the gates legitimately did not run, because
     the pull request touched none of their paths -> success

WHY (3) IS THE HARD ONE
-----------------------
Through the Actions API, a workflow that will never run for this event
and a workflow that has not been created yet look identical: both are an
absence. Reading an absence as "not applicable" too eagerly gives a false
pass; reading it as "still pending" forever gives a hang. This repository
has already shipped four separate guards that could not fail (#79, #82,
#93, #95), two of them written to close that very family, so the
mechanism here is built to make absence *provable* rather than assumed:

  * Absence is never interpreted at all until the run listing for this
    head SHA contains **this job's own run id**. Our run and the gate
    runs are created by GitHub from the same event, together; once we can
    see ourselves in the listing, the listing enumerates that event.
    Before that, everything is pending. This also validates that the head
    SHA we are querying is the one the runs are attached to -- if it were
    not, we would never find ourselves and would fail on the deadline
    rather than pass on an empty list.

  * Even then, absence is only accepted when the workflow's own
    ``paths:`` filter, evaluated here against the files this pull request
    changes, says the workflow could not have been triggered. Absence
    that the filter does not explain stays pending and ends as a
    *failure* on the deadline. There is no code path in which "I did not
    find it" becomes a pass on its own.

  * The converse disagreement resolves the safe way too: if a run exists
    for a workflow this script did not expect, it is waited for and its
    conclusion is used. A bug in the glob matching below can therefore
    cost an unnecessary wait, but it cannot manufacture a pass.

A run that exists and concluded anything other than ``success`` -- including
``skipped``, which for a whole run means every job was skipped and no gate
actually executed -- is a failure.

RE-RUNS
-------
Branch protection reads the latest check for a context. If you re-run a
gate workflow after a failure, re-run this job too ("Re-run failed jobs"
on the Repo Gate run); it will re-read the gates' current conclusions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

#: The path-filtered workflows whose outcome this check reports.
#:
#: ``repo-gate.yml`` is deliberately absent: it is this script's own
#: workflow, it has no path filter, and its coverage job is required
#: directly. Aggregating it would mean waiting for ourselves.
#:
#: ``backend/tests/scripts/test_gate_coverage.py`` pins this tuple against
#: its ``REQUIRED_WORKFLOWS``, so the two lists cannot drift apart: a
#: workflow that is required but not aggregated here is a gate nothing
#: waits for.
AGGREGATED_WORKFLOWS = (
    ".github/workflows/backend-ci.yml",
    ".github/workflows/python-client-ci.yml",
)

#: GitHub stops evaluating ``paths:`` filters on very large pull requests
#: and runs the workflow instead. Past this many changed files this script
#: stops trusting its own filter evaluation and expects every workflow to
#: run, which is the safe direction: it waits rather than excuses.
PATH_FILTER_FILE_LIMIT = 300

PENDING = "pending"
PASSED = "passed"
FAILED = "failed"
NOT_APPLICABLE = "not-applicable"

#: Conclusions that mean the gate ran and was fine. Everything else that
#: is terminal is a failure, listed nowhere: an allow-list here is the one
#: place a new GitHub conclusion string could quietly become a pass.
SUCCESSFUL_CONCLUSIONS = frozenset({"success"})


# ---------------------------------------------------------------------------
# Path filters
#
# Shared with backend/tests/scripts/test_gate_coverage.py, which imports
# these rather than keeping a second copy. Two implementations of "would
# this workflow trigger" is two answers to the only question that matters
# here.
# ---------------------------------------------------------------------------


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """GitHub path-filter glob -> regex. ``**`` spans separators, ``*`` does not."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif char == "*":
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return re.compile("".join(out) + r"\Z")


def load_workflow(workflow_rel: str, repo_root: Path | None = None) -> dict:
    root = repo_root if repo_root is not None else REPO_ROOT
    return yaml.safe_load((root / workflow_rel).read_text(encoding="utf-8"))


def workflow_triggers(workflow: dict) -> dict:
    """The ``on:`` block of a parsed workflow.

    PyYAML is a YAML 1.1 parser, in which the bare key ``on`` is the
    boolean ``True``. Reading ``workflow["on"]`` returns nothing, every
    path filter silently reads as absent, and every workflow looks
    unfiltered -- which here would mean "expect everything to run" and, in
    test_gate_coverage.py, "every file is reachable". One spelling of this
    mistake hangs, the other is vacuous.
    """
    block = workflow.get("on", workflow.get(True))
    return block if isinstance(block, dict) else {}


def workflow_triggers_on(workflow: dict, rel_path: str) -> bool:
    """Would editing ``rel_path`` start this workflow's pull_request run?"""
    triggers = workflow_triggers(workflow)
    if "pull_request" not in triggers:
        return False
    pull_request = triggers["pull_request"]
    if not isinstance(pull_request, dict):
        # ``pull_request:`` with an empty body means every pull request.
        return True
    ignored = pull_request.get("paths-ignore")
    if ignored and any(glob_to_regex(pattern).match(rel_path) for pattern in ignored):
        return False
    included = pull_request.get("paths")
    if not included:
        return True
    return any(glob_to_regex(pattern).match(rel_path) for pattern in included)


def workflow_is_expected(workflow: dict, changed_files: list[str], *, files_truncated: bool = False) -> bool:
    """Would this pull request's file list start this workflow?"""
    if files_truncated:
        return True
    return any(workflow_triggers_on(workflow, path) for path in changed_files)


# ---------------------------------------------------------------------------
# Decision core -- pure, so that it can be tested without a network
# ---------------------------------------------------------------------------


def _latest_run(runs: list[dict], workflow_rel: str) -> dict | None:
    """The most recent pull_request run of ``workflow_rel`` in ``runs``.

    Restricted to ``event == "pull_request"`` so that a manual
    ``workflow_dispatch`` re-run at the same commit cannot stand in for
    the run this pull request actually produced.
    """
    mine = [run for run in runs if run.get("path") == workflow_rel and run.get("event") == "pull_request"]
    if not mine:
        return None
    return max(mine, key=lambda run: (str(run.get("run_started_at") or ""), int(run.get("id") or 0)))


def classify_workflow(
    workflow_rel: str,
    *,
    runs: list[dict],
    path_expected: bool,
    listing_settled: bool,
) -> tuple[str, str]:
    """One workflow's state, as (state, human-readable reason).

    ``listing_settled`` is the interlock described in the module
    docstring: True only once the caller has seen its own run in the same
    listing. While it is False no absence means anything and everything
    unseen is pending.
    """
    run = _latest_run(runs, workflow_rel)
    if run is not None:
        status = run.get("status")
        if status != "completed":
            return PENDING, f"run {run.get('id')} is {status}"
        conclusion = run.get("conclusion")
        if conclusion is None:
            # Completed with no conclusion is a transient state of the API,
            # not an outcome. Waiting is right; treating it as anything
            # else is guessing.
            return PENDING, f"run {run.get('id')} is completed with no conclusion yet"
        if conclusion in SUCCESSFUL_CONCLUSIONS:
            return PASSED, f"run {run.get('id')} concluded {conclusion}"
        return FAILED, f"run {run.get('id')} concluded {conclusion}"

    if not listing_settled:
        return PENDING, "the run listing does not yet contain this job's own run"
    if path_expected:
        return PENDING, "this pull request matches its paths: filter but no run has appeared"
    return NOT_APPLICABLE, "no file in this pull request matches its paths: filter"


def overall_state(states: dict[str, tuple[str, str]]) -> str:
    """Fold per-workflow states into one.

    Failure beats pending beats success, so a gate that has already failed
    is reported without waiting out the rest.
    """
    values = {state for state, _ in states.values()}
    if FAILED in values:
        return FAILED
    if PENDING in values:
        return PENDING
    return PASSED


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------


def _api_fetch(base_url: str, token: str | None) -> Callable[[str], object]:
    def fetch(path: str) -> object:
        request = urllib.request.Request(f"{base_url}{path}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "tckdb-aggregate-pr-gates")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    return fetch


def fetch_changed_files(
    fetch: Callable[[str], object],
    repo: str,
    pr_number: int,
    *,
    limit: int = PATH_FILTER_FILE_LIMIT,
) -> tuple[list[str], bool]:
    """(paths changed by the pull request, whether the list hit ``limit``)."""
    files: list[str] = []
    page = 1
    while len(files) < limit:
        payload = fetch(f"/repos/{repo}/pulls/{pr_number}/files?per_page=100&page={page}")
        if not isinstance(payload, list) or not payload:
            return files, False
        for entry in payload:
            filename = entry.get("filename")
            if filename:
                files.append(filename)
            previous = entry.get("previous_filename")
            if previous:
                # A rename changes two paths as far as a filter is concerned.
                files.append(previous)
        if len(payload) < 100:
            return files, False
        page += 1
    return files, True


def fetch_runs(fetch: Callable[[str], object], repo: str, head_sha: str) -> list[dict]:
    runs: list[dict] = []
    page = 1
    while True:
        payload = fetch(f"/repos/{repo}/actions/runs?head_sha={head_sha}&per_page=100&page={page}")
        if not isinstance(payload, dict):
            return runs
        batch = payload.get("workflow_runs") or []
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def aggregate(
    *,
    fetch: Callable[[str], object],
    repo: str,
    pr_number: int,
    head_sha: str,
    own_run_id: int,
    workflows: tuple[str, ...] = AGGREGATED_WORKFLOWS,
    repo_root: Path | None = None,
    deadline_seconds: float = 95 * 60,
    poll_seconds: float = 20.0,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> tuple[bool, dict[str, tuple[str, str]]]:
    """Poll until every aggregated workflow is decided. (ok, per-workflow states)."""
    parsed = {rel: load_workflow(rel, repo_root) for rel in workflows}

    changed_files, files_truncated = fetch_changed_files(fetch, repo, pr_number)
    log(f"pull request #{pr_number} changes {len(changed_files)} path(s) at head {head_sha}")
    if files_truncated:
        log(
            f"more than {PATH_FILTER_FILE_LIMIT} changed paths; GitHub stops evaluating "
            "paths: filters on pull requests this large, so every gate is expected to run"
        )
    for path in sorted(set(changed_files))[:50]:
        log(f"  changed: {path}")

    expected = {
        rel: workflow_is_expected(parsed[rel], changed_files, files_truncated=files_truncated) for rel in workflows
    }
    for rel in workflows:
        log(f"paths: filter for {rel} -> {'expected to run' if expected[rel] else 'not triggered by this pull request'}")

    started = now()
    states: dict[str, tuple[str, str]] = {}
    announced: dict[str, str] = {}

    while True:
        runs = fetch_runs(fetch, repo, head_sha)
        listing_settled = any(int(run.get("id") or 0) == own_run_id for run in runs)
        states = {
            rel: classify_workflow(
                rel,
                runs=runs,
                path_expected=expected[rel],
                listing_settled=listing_settled,
            )
            for rel in workflows
        }
        for rel, (state, reason) in states.items():
            if announced.get(rel) != state:
                log(f"{rel}: {state} ({reason})")
                announced[rel] = state

        overall = overall_state(states)
        if overall != PENDING:
            return overall == PASSED, states

        if now() - started >= deadline_seconds:
            log(
                "deadline reached with gates undecided. This is a failure, not a "
                "pass: an absent run that the paths: filter does not explain has "
                "no benign reading."
            )
            return False, states

        if not listing_settled:
            log(f"waiting: run {own_run_id} (this job) is not in the run listing for {head_sha} yet")
        sleep(poll_seconds)


def _write_summary(states: dict[str, tuple[str, str]], ok: bool) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["## Required gates", "", "| Workflow | State | Detail |", "| --- | --- | --- |"]
    for rel, (state, reason) in sorted(states.items()):
        lines.append(f"| `{rel}` | {state} | {reason} |")
    lines.append("")
    lines.append("Result: **" + ("success" if ok else "failure") + "**")
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr-number", type=int, default=0)
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--run-id", type=int, default=int(os.environ.get("GITHUB_RUN_ID") or 0))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--deadline-minutes", type=float, default=95.0)
    parser.add_argument("--poll-seconds", type=float, default=20.0)
    args = parser.parse_args(argv)

    if args.event_name != "pull_request":
        # Required status checks are evaluated on pull requests. On a push
        # to main every gate workflow fires under its own push trigger and
        # is visible on the commit directly, so there is nothing here to
        # aggregate. This branch is unreachable on the event this check
        # exists for.
        print(f"event is {args.event_name!r}, not 'pull_request'; nothing to aggregate")
        return 0

    missing = [
        name
        for name, value in (
            ("--repo", args.repo),
            ("--pr-number", args.pr_number),
            ("--head-sha", args.head_sha),
            ("--run-id", args.run_id),
        )
        if not value
    ]
    if missing:
        print(f"refusing to report on incomplete input; missing {', '.join(missing)}", file=sys.stderr)
        return 2

    fetch = _api_fetch(os.environ.get("GITHUB_API_URL", "https://api.github.com"), os.environ.get("GITHUB_TOKEN"))
    try:
        ok, states = aggregate(
            fetch=fetch,
            repo=args.repo,
            pr_number=args.pr_number,
            head_sha=args.head_sha,
            own_run_id=args.run_id,
            deadline_seconds=args.deadline_minutes * 60,
            poll_seconds=args.poll_seconds,
        )
    except urllib.error.HTTPError as error:
        print(f"GitHub API error {error.code} for {error.url}", file=sys.stderr)
        return 2

    _write_summary(states, ok)
    print("")
    for rel, (state, reason) in sorted(states.items()):
        print(f"{rel}: {state} -- {reason}")
    if not ok:
        failed = [rel for rel, (state, _) in states.items() if state != PASSED and state != NOT_APPLICABLE]
        print(f"required gates did not pass: {', '.join(sorted(failed))}", file=sys.stderr)
        return 1
    print("all required gates passed or do not apply to this pull request")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
