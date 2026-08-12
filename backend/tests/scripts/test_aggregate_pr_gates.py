"""The aggregate gate must be able to fail.

``backend/scripts/aggregate_pr_gates.py`` is the check that branch
protection lists, so every test suite in this repository gates a merge
through it and nothing else. That makes the interesting property not
"does it pass on a good pull request" but "is there any pull request on
which it passes without having checked anything". This repository has
shipped four guards that could not fail (#79 a nightly red nobody read,
#82 a sweep that verified nothing and exited 0, #93 a step that never
executed, #95 three suites run by no job), and two of the guards written
to close that family shipped containing it. So the tests below are
weighted toward the three ways this one could join them:

  * an absent workflow run read as "legitimately skipped" when it is
    merely late -- a false pass;
  * a failing gate whose conclusion is not one of the strings the folder
    treats as failure -- a false pass;
  * a gate that never appears being waited on forever -- a hang, which
    on a required check is indistinguishable from a block.

The decision core is pure and the API is a callable, so all three are
reachable here without a network.

Run under ``--noconftest`` by ``.github/workflows/repo-gate.yml`` as well
as by the backend complement gate, hence the importlib loader below
rather than ``from scripts import ...``: the repo gate has no backend
rootdir and no conftest, and inserting ``backend/scripts`` onto
``sys.path`` there would put ``lib``, ``dev``, ``ops`` and ``validation``
ahead of real packages.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gates = _load_module("aggregate_pr_gates", BACKEND_ROOT / "scripts" / "aggregate_pr_gates.py")

BACKEND_CI = ".github/workflows/backend-ci.yml"
CLIENT_CI = ".github/workflows/python-client-ci.yml"

OWN_RUN_ID = 999


def run(path: str, *, status: str = "completed", conclusion: str | None = "success", run_id: int = 1, **extra):
    payload = {
        "id": run_id,
        "path": path,
        "event": "pull_request",
        "status": status,
        "conclusion": conclusion,
        "run_started_at": "2026-08-12T00:00:00Z",
    }
    payload.update(extra)
    return payload


def own_run():
    """The repo-gate run this job is executing in, as the API reports it."""
    return run(".github/workflows/repo-gate.yml", status="in_progress", conclusion=None, run_id=OWN_RUN_ID)


# ---------------------------------------------------------------------------
# The three states, at the level of one workflow
# ---------------------------------------------------------------------------


def test_a_passing_run_passes():
    state, _ = gates.classify_workflow(
        BACKEND_CI, runs=[own_run(), run(BACKEND_CI)], path_expected=True, listing_settled=True
    )
    assert state == gates.PASSED


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale", "neutral", "skipped"],
)
def test_every_non_success_conclusion_is_a_failure(conclusion):
    """The fold is an allow-list of one, on purpose.

    ``skipped`` is in this list and is the subtle one: a *run* concluding
    skipped means every job in it was skipped, so the gate did not
    execute. Reading that as "fine, nothing to do" is precisely the shape
    of #93.
    """
    state, _ = gates.classify_workflow(
        BACKEND_CI,
        runs=[own_run(), run(BACKEND_CI, conclusion=conclusion)],
        path_expected=True,
        listing_settled=True,
    )
    assert state == gates.FAILED, f"{conclusion!r} must not be read as success"


def test_an_absent_run_the_filter_explains_is_not_applicable():
    state, _ = gates.classify_workflow(CLIENT_CI, runs=[own_run()], path_expected=False, listing_settled=True)
    assert state == gates.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Skipped vs not started -- the distinction the whole design turns on
# ---------------------------------------------------------------------------


def test_absence_means_nothing_until_this_job_can_see_its_own_run():
    """The interlock. Before it, an empty listing is pending, never a pass.

    Without this, a listing that has not yet been populated for the event
    reads as "no gates ran" and every pull request passes instantly --
    the false pass that a required check makes invisible, because a green
    check is what everyone expects to see.
    """
    state, reason = gates.classify_workflow(CLIENT_CI, runs=[], path_expected=False, listing_settled=False)
    assert state == gates.PENDING
    assert "own run" in reason


def test_an_absent_run_the_filter_does_not_explain_stays_pending():
    """Expected but missing is never excused; it waits and then fails."""
    state, _ = gates.classify_workflow(BACKEND_CI, runs=[own_run()], path_expected=True, listing_settled=True)
    assert state == gates.PENDING


def test_a_run_that_exists_is_honoured_even_when_the_filter_did_not_expect_it():
    """Filter disagreement resolves toward checking, not toward excusing.

    A bug in the glob matcher can cost a wait. It must not be able to buy
    a pass for a workflow that actually ran and failed.
    """
    state, _ = gates.classify_workflow(
        BACKEND_CI,
        runs=[own_run(), run(BACKEND_CI, conclusion="failure")],
        path_expected=False,
        listing_settled=True,
    )
    assert state == gates.FAILED


def test_an_unfinished_run_is_pending_not_passed():
    for status in ("queued", "in_progress", "waiting", "requested", "pending"):
        state, _ = gates.classify_workflow(
            BACKEND_CI,
            runs=[own_run(), run(BACKEND_CI, status=status, conclusion=None)],
            path_expected=True,
            listing_settled=True,
        )
        assert state == gates.PENDING, status


def test_completed_without_a_conclusion_is_pending():
    state, _ = gates.classify_workflow(
        BACKEND_CI,
        runs=[own_run(), run(BACKEND_CI, conclusion=None)],
        path_expected=True,
        listing_settled=True,
    )
    assert state == gates.PENDING


def test_the_latest_run_for_a_workflow_wins():
    stale = run(BACKEND_CI, conclusion="failure", run_id=1, run_started_at="2026-08-12T00:00:00Z")
    fresh = run(BACKEND_CI, conclusion="success", run_id=2, run_started_at="2026-08-12T01:00:00Z")
    state, _ = gates.classify_workflow(
        BACKEND_CI, runs=[own_run(), stale, fresh], path_expected=True, listing_settled=True
    )
    assert state == gates.PASSED


def test_a_dispatch_run_does_not_stand_in_for_the_pull_request_run():
    """Otherwise a green manual re-run at the same commit launders a red PR."""
    state, _ = gates.classify_workflow(
        BACKEND_CI,
        runs=[own_run(), run(BACKEND_CI, event="workflow_dispatch")],
        path_expected=True,
        listing_settled=True,
    )
    assert state == gates.PENDING


def test_another_workflows_run_is_not_mistaken_for_this_one():
    state, _ = gates.classify_workflow(
        BACKEND_CI,
        runs=[own_run(), run(CLIENT_CI, conclusion="success")],
        path_expected=True,
        listing_settled=True,
    )
    assert state == gates.PENDING


# ---------------------------------------------------------------------------
# The fold
# ---------------------------------------------------------------------------


def test_failure_beats_pending_beats_success():
    assert gates.overall_state({"a": (gates.PASSED, ""), "b": (gates.FAILED, "")}) == gates.FAILED
    assert gates.overall_state({"a": (gates.PENDING, ""), "b": (gates.FAILED, "")}) == gates.FAILED
    assert gates.overall_state({"a": (gates.PASSED, ""), "b": (gates.PENDING, "")}) == gates.PENDING
    assert gates.overall_state({"a": (gates.PASSED, ""), "b": (gates.NOT_APPLICABLE, "")}) == gates.PASSED


# ---------------------------------------------------------------------------
# Path filters against the real workflows in this repository
# ---------------------------------------------------------------------------


def _real(rel: str) -> dict:
    return gates.load_workflow(rel, REPO_ROOT)


def test_a_paper_only_change_expects_no_gate():
    """Case 3 of the bite tests, as a unit."""
    changed = ["paper/manuscript.tex"]
    for rel in gates.AGGREGATED_WORKFLOWS:
        assert not gates.workflow_is_expected(_real(rel), changed), rel


def test_a_markdown_only_change_outside_docs_expects_no_gate():
    assert not gates.workflow_is_expected(_real(BACKEND_CI), ["README.md"])
    assert not gates.workflow_is_expected(_real(CLIENT_CI), ["README.md"])


def test_a_backend_change_expects_the_backend_gate_and_not_the_client_gate():
    changed = ["backend/app/api/routes/health.py"]
    assert gates.workflow_is_expected(_real(BACKEND_CI), changed)
    assert not gates.workflow_is_expected(_real(CLIENT_CI), changed)


def test_a_client_change_expects_the_client_gate():
    changed = ["clients/python/src/tckdb_client/client.py"]
    assert gates.workflow_is_expected(_real(CLIENT_CI), changed)
    assert not gates.workflow_is_expected(_real(BACKEND_CI), changed)


def test_a_shared_schemas_change_expects_both():
    changed = ["schemas/python/tckdb-schemas/tckdb_schemas/enums.py"]
    for rel in gates.AGGREGATED_WORKFLOWS:
        assert gates.workflow_is_expected(_real(rel), changed), rel


def test_a_rename_counts_both_of_its_paths():
    """A file moved out of backend/ still changed backend/."""
    fetched = []

    def fetch(path: str):
        fetched.append(path)
        if "/files" in path:
            if "page=1" in path:
                return [{"filename": "docs/moved.py", "previous_filename": "backend/app/moved.py"}]
            return []
        return {"workflow_runs": []}

    files, truncated = gates.fetch_changed_files(fetch, "o/r", 1)
    assert not truncated
    assert files == ["docs/moved.py", "backend/app/moved.py"]
    assert gates.workflow_is_expected(_real(BACKEND_CI), files)


def test_a_very_large_pull_request_expects_everything():
    """GitHub stops evaluating paths: filters past its own limit; so do we."""
    assert gates.workflow_is_expected(_real(CLIENT_CI), ["paper/x.tex"], files_truncated=True)


def test_glob_star_does_not_cross_a_separator():
    assert gates.glob_to_regex("docs/*.md").match("docs/a.md")
    assert not gates.glob_to_regex("docs/*.md").match("docs/nested/a.md")
    assert gates.glob_to_regex("docs/**").match("docs/nested/a.md")
    assert not gates.glob_to_regex("backend/**").match("backendish/a.py")


def test_the_on_key_is_read_despite_yaml_1_1_booleans():
    """``on:`` parses as the boolean True. Missing that makes every filter absent.

    In this module the failure direction is a hang rather than a false
    pass -- but only because absence is also cross-checked. Pin it anyway.
    """
    parsed = yaml.safe_load("on:\n  pull_request:\n    paths:\n      - 'backend/**'\n")
    assert "on" not in parsed or parsed.get("on") is None
    assert gates.workflow_triggers(parsed)["pull_request"]["paths"] == ["backend/**"]


def test_a_workflow_without_a_pull_request_trigger_never_expects_to_run():
    parsed = yaml.safe_load("on:\n  schedule:\n    - cron: '0 0 * * *'\n")
    assert not gates.workflow_triggers_on(parsed, "backend/app/x.py")


def test_paths_ignore_is_honoured():
    parsed = yaml.safe_load("on:\n  pull_request:\n    paths-ignore:\n      - '**.md'\n")
    assert not gates.workflow_triggers_on(parsed, "backend/README.md")
    assert gates.workflow_triggers_on(parsed, "backend/app/x.py")


# ---------------------------------------------------------------------------
# The driver, end to end, against a scripted API
# ---------------------------------------------------------------------------


class FakeApi:
    """A GitHub whose run listing changes between polls."""

    def __init__(self, changed: list[str], run_sequence: list[list[dict]]):
        self.changed = changed
        self.run_sequence = run_sequence
        self.run_calls = 0

    def __call__(self, path: str):
        if "/files" in path:
            return [{"filename": name} for name in self.changed] if "page=1" in path else []
        index = min(self.run_calls, len(self.run_sequence) - 1)
        self.run_calls += 1
        return {"workflow_runs": self.run_sequence[index]}


def drive(api: FakeApi, *, deadline_seconds: float = 600.0):
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    def sleep(seconds: float):
        clock["t"] += seconds

    return gates.aggregate(
        fetch=api,
        repo="o/r",
        pr_number=1,
        head_sha="deadbeef",
        own_run_id=OWN_RUN_ID,
        repo_root=REPO_ROOT,
        deadline_seconds=deadline_seconds,
        poll_seconds=20.0,
        now=now,
        sleep=sleep,
        log=lambda _message: None,
    )


def test_docs_only_pull_request_passes_without_waiting_for_anything():
    api = FakeApi(["paper/manuscript.tex"], [[own_run()]])
    ok, states = drive(api)
    assert ok
    assert {state for state, _ in states.values()} == {gates.NOT_APPLICABLE}


def test_docs_only_pull_request_does_not_pass_before_the_listing_settles():
    """The first poll sees an empty listing; passing there would be the bug."""
    api = FakeApi(["paper/manuscript.tex"], [[], [], [own_run()]])
    ok, _ = drive(api)
    assert ok
    assert api.run_calls == 3, "it must not have concluded on the empty listings"


def test_a_backend_pull_request_waits_and_then_fails_on_a_red_gate():
    api = FakeApi(
        ["backend/app/x.py"],
        [
            [own_run()],
            [own_run(), run(BACKEND_CI, status="in_progress", conclusion=None)],
            [own_run(), run(BACKEND_CI, conclusion="failure")],
        ],
    )
    ok, states = drive(api)
    assert not ok
    assert states[BACKEND_CI][0] == gates.FAILED
    assert states[CLIENT_CI][0] == gates.NOT_APPLICABLE


def test_a_backend_pull_request_passes_on_a_green_gate():
    api = FakeApi(["backend/app/x.py"], [[own_run()], [own_run(), run(BACKEND_CI, conclusion="success")]])
    ok, states = drive(api)
    assert ok
    assert states[BACKEND_CI][0] == gates.PASSED


def test_a_gate_that_never_appears_fails_on_the_deadline_rather_than_passing():
    """The hang is converted into a failure. It is never converted into a pass."""
    api = FakeApi(["backend/app/x.py"], [[own_run()]])
    ok, states = drive(api, deadline_seconds=60.0)
    assert not ok
    assert states[BACKEND_CI][0] == gates.PENDING


def test_a_listing_that_never_settles_fails_on_the_deadline():
    """If the head SHA were wrong we would never see ourselves. That must fail."""
    api = FakeApi(["paper/manuscript.tex"], [[]])
    ok, _ = drive(api, deadline_seconds=60.0)
    assert not ok


def test_aggregating_nothing_is_refused_rather_than_folded_to_success():
    """A fold over an empty list returns success. That must not be reachable."""
    api = FakeApi(["backend/app/x.py"], [[own_run()]])
    with pytest.raises(ValueError, match="not a check"):
        gates.aggregate(
            fetch=api,
            repo="o/r",
            pr_number=1,
            head_sha="deadbeef",
            own_run_id=OWN_RUN_ID,
            workflows=(),
            repo_root=REPO_ROOT,
            log=lambda _message: None,
        )


def test_a_red_gate_short_circuits_the_wait():
    """Failure is reported without waiting out a sibling that is still running."""
    api = FakeApi(
        ["schemas/python/tckdb-schemas/x.py"],
        [[own_run(), run(BACKEND_CI, conclusion="failure"), run(CLIENT_CI, status="in_progress", conclusion=None)]],
    )
    ok, _ = drive(api)
    assert not ok
    assert api.run_calls == 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def test_a_non_pull_request_event_reports_nothing_and_does_not_pretend_to():
    assert gates.main(["--event-name", "push", "--repo", "o/r"]) == 0


def test_incomplete_input_is_refused_rather_than_passed():
    """Missing arguments must not become "no gates found, all good"."""
    assert gates.main(["--event-name", "pull_request", "--repo", "o/r", "--pr-number", "0"]) == 2
    assert (
        gates.main(
            ["--event-name", "pull_request", "--repo", "", "--pr-number", "3", "--head-sha", "x", "--run-id", "1"]
        )
        == 2
    )
