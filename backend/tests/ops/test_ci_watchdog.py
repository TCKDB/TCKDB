"""A notification path nobody has ever seen fire is not a notification path.

The CI watchdog exists because backend-nightly.yml failed eleven nights
running and nothing said so. Testing it by reading it would repeat the
original mistake one level up, so it is run as a real process against a
real socket that stands in for the GitHub API, ntfy and the dead man's
switch, and every test asserts on *what left the machine*.

Three rules are load-bearing here and each has been got wrong in the wild:

    A workflow that stops running is a fault, not a pass.
    A verdict that has not changed is not news.
    A push that was not delivered was not sent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from tests.ops.conftest import CI_WATCHDOG, OPS_DIR, WORKFLOWS_DIR

WORKFLOW = "backend-nightly.yml"
RUNS_PATH = f"/gh/repos/TCKDB/TCKDB/actions/workflows/{WORKFLOW}/runs"
JOBS_PREFIX = "/gh/repos/TCKDB/TCKDB/actions/runs/"
NTFY = "/ntfy"


def ago(hours: float) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(hours=hours)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(
    conclusion: str | None,
    *,
    hours_ago: float,
    run_id: int = 100,
    sha: str = "ec53167e7ccc1e34367b66490e00bb6644ccae79",
    status: str = "completed",
) -> dict:
    return {
        "id": run_id,
        "name": "Backend Nightly (full suite)",
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "run_started_at": ago(hours_ago),
        "created_at": ago(hours_ago),
        "html_url": f"https://github.com/TCKDB/TCKDB/actions/runs/{run_id}",
    }


def serve_runs(fake_host, runs: list[dict]) -> None:
    fake_host.set_json(RUNS_PATH, {"total_count": len(runs), "workflow_runs": runs})


def serve_jobs(fake_host, failed_step: str = "Full test suite") -> None:
    fake_host.set_json(
        JOBS_PREFIX,
        {
            "jobs": [
                {
                    "name": "Full test suite",
                    "conclusion": "failure",
                    "steps": [
                        {"name": "Check out repository", "conclusion": "success"},
                        {"name": failed_step, "conclusion": "failure"},
                    ],
                }
            ]
        },
    )


def pushes(fake_host) -> list:
    return fake_host.paths(NTFY)


def titles(fake_host) -> list[str]:
    return [r.headers.get("title", "") for r in pushes(fake_host)]


def red_history(nights: int) -> list[dict]:
    """`nights` consecutive nightly failures, most recent first."""
    return [
        run("failure", hours_ago=2 + 24 * i, run_id=100 - i)
        for i in range(nights)
    ]


# ---------------------------------------------------------------------------
# A red workflow becomes a push that can be acted on
# ---------------------------------------------------------------------------


def test_a_failing_workflow_is_pushed_with_enough_to_act_on(fake_host, run_ci_watchdog):
    """"Nightly failed" with no link is how people learn to ignore alerts.

    The push has to name the workflow, the commit, the failing step and the
    run URL, or the recipient still has to go and find all of it -- and the
    reason this alert did not already exist is that nobody was going to look.
    """
    serve_runs(fake_host, red_history(11))
    serve_jobs(fake_host)

    proc = run_ci_watchdog()
    assert proc.returncode == 1, proc.stderr

    sent = pushes(fake_host)
    assert len(sent) == 1, "a fresh red verdict is news exactly once"
    body = sent[0].body
    assert WORKFLOW in sent[0].headers.get("title", "")
    assert "11" in sent[0].headers.get("title", ""), "the streak belongs in the title"
    assert "https://github.com/TCKDB/TCKDB/actions/runs/100" in body
    assert "ec53167e7ccc" in body, "the commit under test"
    assert "Full test suite" in body, "the step that actually failed"
    assert "Full test suite / Full test suite" not in body, (
        "a job whose only step shares its name says it once"
    )


def test_the_failing_step_is_named_within_its_job(fake_host, run_ci_watchdog):
    serve_runs(fake_host, red_history(1))
    fake_host.set_json(
        JOBS_PREFIX,
        {
            "jobs": [
                {"name": "lint", "conclusion": "success", "steps": []},
                {
                    "name": "full-suite",
                    "conclusion": "failure",
                    "steps": [{"name": "Initialize artifact bucket", "conclusion": "failure"}],
                },
            ]
        },
    )
    run_ci_watchdog()
    body = pushes(fake_host)[0].body
    assert "full-suite / Initialize artifact bucket" in body
    assert "lint" not in body, "a job that passed is not part of the diagnosis"


def test_the_streak_counts_only_the_leading_failures(fake_host, run_ci_watchdog):
    serve_runs(
        fake_host,
        [
            run("failure", hours_ago=2, run_id=100),
            run("failure", hours_ago=26, run_id=99),
            run("success", hours_ago=50, run_id=98),
            run("failure", hours_ago=74, run_id=97),
        ],
    )
    serve_jobs(fake_host)
    run_ci_watchdog()
    assert "(2 consecutive runs)" in titles(fake_host)[0]


def test_cancelled_runs_are_evidence_of_nothing(fake_host, run_ci_watchdog):
    """A cancelled run must break neither a red streak nor a green one.

    Counting it as green would silently clear a real breakage -- the alert
    would say "green again" while the suite had not passed since July.
    """
    serve_runs(
        fake_host,
        [
            run("cancelled", hours_ago=1, run_id=101),
            run("failure", hours_ago=2, run_id=100),
            run("failure", hours_ago=26, run_id=99),
        ],
    )
    serve_jobs(fake_host)
    proc = run_ci_watchdog()
    assert proc.returncode == 1
    assert "(2 consecutive runs)" in titles(fake_host)[0]


# ---------------------------------------------------------------------------
# Edge triggering: eleven identical failures are not eleven pushes
# ---------------------------------------------------------------------------


def test_an_unchanged_verdict_is_not_re_announced(fake_host, run_ci_watchdog):
    serve_runs(fake_host, red_history(3))
    serve_jobs(fake_host)

    run_ci_watchdog()
    assert len(pushes(fake_host)) == 1

    serve_runs(fake_host, red_history(4))
    second = run_ci_watchdog()
    assert second.returncode == 1, "still red, still a red job"
    assert len(pushes(fake_host)) == 1, (
        "a breakage that has not changed is not news; an alert you learn to "
        "ignore is worse than no alert"
    )


def test_a_long_breakage_is_reminded_about_periodically(fake_host, run_ci_watchdog):
    """Edge triggering must not become permanent silence either."""
    serve_runs(fake_host, red_history(1))
    serve_jobs(fake_host)
    run_ci_watchdog(env_overrides={"TCKDB_WATCHDOG_REMINDER_RUNS": "7"})
    assert len(pushes(fake_host)) == 1

    serve_runs(fake_host, red_history(7))
    run_ci_watchdog(env_overrides={"TCKDB_WATCHDOG_REMINDER_RUNS": "7"})
    assert len(pushes(fake_host)) == 1, "still inside the first reminder window"

    serve_runs(fake_host, red_history(8))
    run_ci_watchdog(env_overrides={"TCKDB_WATCHDOG_REMINDER_RUNS": "7"})
    assert len(pushes(fake_host)) == 2, "a week of red earns one more mention"
    assert "(8 consecutive runs)" in titles(fake_host)[1]


def test_recovery_is_announced(fake_host, run_ci_watchdog):
    """Otherwise you are left wondering whether it is still broken."""
    serve_runs(fake_host, red_history(3))
    serve_jobs(fake_host)
    run_ci_watchdog()

    serve_runs(fake_host, [run("success", hours_ago=1, run_id=200), *red_history(3)])
    proc = run_ci_watchdog()
    assert proc.returncode == 0
    assert len(pushes(fake_host)) == 2
    assert "green again" in titles(fake_host)[1]


def test_a_first_look_at_a_healthy_workflow_says_nothing(fake_host, run_ci_watchdog):
    serve_runs(fake_host, [run("success", hours_ago=2, run_id=200)])
    proc = run_ci_watchdog()
    assert proc.returncode == 0
    assert pushes(fake_host) == [], "a non-event is not an alert"
    assert (run_ci_watchdog.state_dir / WORKFLOW).read_text() == "green", (
        "but the state is recorded, so a later break is still an edge"
    )


# ---------------------------------------------------------------------------
# Silence is the failure mode that looks like success
# ---------------------------------------------------------------------------


def test_a_workflow_that_stopped_running_is_a_fault(fake_host, run_ci_watchdog):
    """The case a failure-only monitor reports as healthy.

    Every run in the history passed. There simply has not been one for
    three days, which is what a disabled schedule looks like -- and it is
    exactly how GitHub leaves a repository after 60 days of inactivity.
    """
    serve_runs(fake_host, [run("success", hours_ago=72, run_id=200)])
    proc = run_ci_watchdog()
    assert proc.returncode == 1
    assert len(pushes(fake_host)) == 1
    title = titles(fake_host)[0]
    assert "has not run" in title
    body = pushes(fake_host)[0].body
    assert "60 days" in body, "the push has to name the most likely cause"
    assert f"actions/workflows/{WORKFLOW}" in body, "and where to re-enable it"


def test_a_still_running_workflow_is_not_silence(fake_host, run_ci_watchdog):
    """A slow nightly overlapping the watchdog must not page.

    The in-progress run has no conclusion, so a freshness check that only
    looked at completed runs would call a workflow that is running right
    now "not run for 72 hours".
    """
    serve_runs(
        fake_host,
        [
            run(None, hours_ago=1, run_id=201, status="in_progress"),
            run("success", hours_ago=72, run_id=200),
        ],
    )
    proc = run_ci_watchdog()
    assert proc.returncode == 0
    assert pushes(fake_host) == []


def test_an_unparseable_run_time_is_not_read_as_fresh(fake_host, run_ci_watchdog):
    """A freshness check that does not happen reads as "not silent"."""
    broken = run("success", hours_ago=1, run_id=200)
    broken["run_started_at"] = "whenever"
    broken["created_at"] = "whenever"
    serve_runs(fake_host, [broken])
    proc = run_ci_watchdog()
    assert "freshness was not checked" in proc.stderr


def test_a_workflow_that_has_never_run_is_reported(fake_host, run_ci_watchdog):
    serve_runs(fake_host, [])
    proc = run_ci_watchdog()
    assert proc.returncode == 1
    assert "has never run" in titles(fake_host)[0]


# ---------------------------------------------------------------------------
# A push that never arrived must not be recorded as delivered
# ---------------------------------------------------------------------------


def test_failed_push_is_retried_on_the_next_run(fake_host, run_ci_watchdog):
    """State advances on delivery, not on intent.

    Edge triggering means a missed transition is missed until the next
    reminder window -- up to a week of a red suite and a quiet phone. So
    the state file only moves once ntfy has accepted the push. This bug
    was found and fixed in the Pi-side checker; it is not being rebuilt.
    """
    serve_runs(fake_host, red_history(2))
    serve_jobs(fake_host)
    fake_host.failing_paths.add(f"{NTFY}/test-topic")

    first = run_ci_watchdog()
    assert first.returncode == 1
    assert "state not advanced" in first.stderr
    assert not (run_ci_watchdog.state_dir / WORKFLOW).exists(), (
        "an undelivered alert must not be recorded as delivered"
    )

    fake_host.failing_paths.clear()
    second = run_ci_watchdog()
    assert second.returncode == 1
    assert (run_ci_watchdog.state_dir / WORKFLOW).read_text().startswith("red:")
    delivered = [r for r in pushes(fake_host) if r.method == "POST"]
    assert len(delivered) >= 2, "one failed attempt, one successful retry"


# ---------------------------------------------------------------------------
# ... and the watchdog's own death is silence, deliberately
# ---------------------------------------------------------------------------


def test_a_watchdog_that_cannot_start_sends_no_ping(fake_host, run_ci_watchdog):
    serve_runs(fake_host, red_history(2))
    proc = run_ci_watchdog(
        env_overrides={
            "TCKDB_NTFY_TOPIC": "",
            "TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman",
        }
    )
    assert proc.returncode == 2
    assert fake_host.paths("/deadman") == [], (
        "a watchdog that refused to run must stay silent so its silence is the alarm"
    )


def test_an_unreadable_run_history_is_loud_and_unpinged(fake_host, run_ci_watchdog):
    """A 404 or a token without actions:read parses to zero runs.

    Reported as "has never run" that would be true-sounding, wrong, and
    point at the wrong fix -- so the API error is handled before the run
    list is believed, and no heartbeat is forged for a verdict never reached.

    Exit 2, not 1: the only watched workflow was never looked at, which is
    what this script's own header has always said 2 means and what
    verify_artifact_integrity.py means by it. Nothing here is evidence that
    a workflow is red.
    """
    fake_host.set_json(RUNS_PATH, {"message": "Not Found"}, code=404)
    proc = run_ci_watchdog(
        env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"}
    )
    assert proc.returncode == 2
    assert pushes(fake_host) == []
    assert fake_host.paths("/deadman") == []
    assert "could not read run history" in proc.stderr


def test_an_unparseable_run_history_is_not_read_as_green(fake_host, run_ci_watchdog):
    """The failure this whole file is about, one level in.

    An empty parse is indistinguishable from "no failures", so a jq that
    errors would make the watchdog report a broken nightly as healthy --
    monitoring vouching against the outage it exists to find. A 200 with
    the wrong shape has to be as loud as a 404.
    """
    fake_host.set_json(RUNS_PATH, {"workflow_runs": {"not": "an array"}})
    proc = run_ci_watchdog(
        env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"}
    )
    assert proc.returncode == 2, "not checked, rather than checked and found red"
    assert pushes(fake_host) == []
    assert fake_host.paths("/deadman") == []
    assert "refusing to read it as green" in proc.stderr


def test_deadman_is_pinged_even_when_a_watched_workflow_is_red(fake_host, run_ci_watchdog):
    """The ping means "this watchdog ran", never "CI is well".

    Tying it to a green verdict would let a red nightly silence the
    heartbeat, and the external service would page "the watchdog is gone"
    while the watchdog was busy naming the workflow that failed.
    """
    serve_runs(fake_host, red_history(2))
    serve_jobs(fake_host)
    proc = run_ci_watchdog(
        env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"}
    )
    assert proc.returncode == 1
    assert len(fake_host.paths("/deadman")) == 1
    assert len(pushes(fake_host)) == 1


def test_a_missing_deadman_is_named_as_a_gap(fake_host, run_ci_watchdog):
    serve_runs(fake_host, [run("success", hours_ago=2, run_id=200)])
    proc = run_ci_watchdog()
    assert "TCKDB_DEADMAN_URL is unset" in proc.stderr


# ---------------------------------------------------------------------------
# A watchdog that watched nothing is not a watchdog that found nothing
# ---------------------------------------------------------------------------


UPTIME_RUNS = "/gh/repos/TCKDB/TCKDB/actions/workflows/uptime-check.yml/runs"


def test_a_watch_list_that_parses_to_nothing_is_not_a_pass(fake_host, run_ci_watchdog):
    """The defect: every entry skipped, and the run still called clean.

    ``backend-nightly.yml`` without ``:hours`` is malformed, so the loop
    body never ran; nothing set the "no verdict" flag, so the dead man's
    ping went out anyway and the external monitor vouched for a checker
    that had checked nothing. A watchdog forging its own heartbeat is
    worse than no watchdog, because it is believed.
    """
    serve_runs(fake_host, red_history(2))
    proc = run_ci_watchdog(
        env_overrides={
            "TCKDB_WATCHED_WORKFLOWS": "backend-nightly.yml uptime-check.yml",
            "TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman",
        }
    )
    assert proc.returncode == 2, "nothing was checked; that is not a pass"
    assert fake_host.paths("/deadman") == [], (
        "the heartbeat asserts that a check happened, and none did"
    )
    assert pushes(fake_host) == []
    assert "verdicts=0" in proc.stdout, "the count is reported, not implied"
    assert "NOT CHECKED" in proc.stderr


def test_a_watch_list_of_pure_whitespace_watches_nothing_and_says_so(
    fake_host, run_ci_watchdog
):
    """The zero-iteration case in its purest form.

    A list of one space is non-empty, so it does not reach the built-in
    default, and it splits into no entries at all: the loop body never
    runs, nothing is malformed, nothing errors, and every counter the
    script keeps stays at its starting value. This is the case where only
    the verdict count itself can tell the difference between a clean run
    and a run that never began.
    """
    serve_runs(fake_host, red_history(2))
    proc = run_ci_watchdog(
        env_overrides={
            "TCKDB_WATCHED_WORKFLOWS": "   ",
            "TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman",
        }
    )
    assert proc.returncode == 2
    assert fake_host.paths("/deadman") == []
    assert "watched=0 verdicts=0 unchecked=0" in proc.stdout
    assert "NOT CHECKED" in proc.stderr


def test_an_empty_watch_list_falls_back_to_the_built_in_default(
    fake_host, run_ci_watchdog
):
    """An unset or empty list is not an empty list; it is the default one.

    Worth pinning, because the obvious reading of the zero-verdict bug is
    "empty input means watch nothing" -- and fixing that reading instead
    of the real one would leave the malformed path exactly as it was.
    """
    serve_runs(fake_host, [run("success", hours_ago=2, run_id=200)])
    fake_host.set_json(UPTIME_RUNS, {"workflow_runs": [run("success", hours_ago=0.2, run_id=300)]})

    proc = run_ci_watchdog(
        env_overrides={
            "TCKDB_WATCHED_WORKFLOWS": "",
            "TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman",
        }
    )
    assert proc.returncode == 0
    assert "verdicts=2" in proc.stdout, "both default workflows were watched"
    assert len(fake_host.paths("/deadman")) == 1
    assert (run_ci_watchdog.state_dir / "uptime-check.yml").read_text() == "green"


def test_one_unwatchable_entry_does_not_pass_on_the_others(fake_host, run_ci_watchdog):
    """A workflow the operator asked for and did not get is a fault.

    A green nightly says nothing about the uptime check, so a run that
    silently dropped the second entry must not exit 0 on the strength of
    the first. It exits 2 -- part of the scope was not checked -- which is
    the same sentence verify_artifact_integrity.py's exit 2 says.
    """
    serve_runs(fake_host, [run("success", hours_ago=2, run_id=200)])
    proc = run_ci_watchdog(
        env_overrides={
            "TCKDB_WATCHED_WORKFLOWS": f"{WORKFLOW}:30 uptime-check.yml",
            "TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman",
        }
    )
    assert proc.returncode == 2
    assert "watched=2 verdicts=1 unchecked=1" in proc.stdout
    assert fake_host.paths("/deadman") == [], (
        "half a check is not the completed run the heartbeat claims"
    )


def test_a_red_workflow_outranks_an_unchecked_one(fake_host, run_ci_watchdog):
    """1 before 2 when both are true, as in verify_artifact_integrity.py.

    "Something is red" is the more actionable of the two findings, and the
    unchecked entry is still named on stderr for whoever triages it.
    """
    serve_runs(fake_host, red_history(2))
    serve_jobs(fake_host)
    fake_host.set_json(UPTIME_RUNS, {"message": "Not Found"}, code=404)

    proc = run_ci_watchdog(
        env_overrides={"TCKDB_WATCHED_WORKFLOWS": f"{WORKFLOW}:30 uptime-check.yml:6"}
    )
    assert proc.returncode == 1
    assert "watched=2 verdicts=1 unchecked=1" in proc.stdout
    assert "could not read run history for uptime-check.yml" in proc.stderr
    assert len(pushes(fake_host)) == 1, "the workflow it could see is still reported"


def test_the_verdict_count_is_reported_on_a_clean_run(fake_host, run_ci_watchdog):
    """Reported unconditionally, so a pass carries its own evidence."""
    serve_runs(fake_host, [run("success", hours_ago=2, run_id=200)])
    proc = run_ci_watchdog()
    assert proc.returncode == 0
    assert "watched=1 verdicts=1 unchecked=0" in proc.stdout
    assert "verdicts=1/1" in proc.stdout, "and again on the closing line"


# ---------------------------------------------------------------------------
# The topic is password-equivalent and this repository is public
# ---------------------------------------------------------------------------


def test_the_topic_never_reaches_the_log(fake_host, run_ci_watchdog):
    serve_runs(fake_host, red_history(2))
    serve_jobs(fake_host)
    proc = run_ci_watchdog(env_overrides={"TCKDB_NTFY_TOPIC": "tckdb-secret-topic"})
    assert "tckdb-secret-topic" not in proc.stdout
    assert "tckdb-secret-topic" not in proc.stderr


def test_the_script_holds_no_topic_of_its_own():
    body = CI_WATCHDOG.read_text()
    assert "ntfy.sh/tckdb-" not in body
    assert body.isascii(), "runtime strings stay ASCII"


# ---------------------------------------------------------------------------
# Several watched workflows, independently
# ---------------------------------------------------------------------------


def test_each_watched_workflow_gets_its_own_verdict(fake_host, run_ci_watchdog):
    """A green uptime-check must not mask a red nightly, or vice versa."""
    serve_runs(fake_host, red_history(2))
    serve_jobs(fake_host)
    fake_host.set_json(
        "/gh/repos/TCKDB/TCKDB/actions/workflows/uptime-check.yml/runs",
        {"workflow_runs": [run("success", hours_ago=0.2, run_id=300)]},
    )

    proc = run_ci_watchdog(
        env_overrides={
            "TCKDB_WATCHED_WORKFLOWS": f"{WORKFLOW}:30 uptime-check.yml:6",
        }
    )
    assert proc.returncode == 1
    assert len(pushes(fake_host)) == 1
    assert WORKFLOW in titles(fake_host)[0]
    assert (run_ci_watchdog.state_dir / "uptime-check.yml").read_text() == "green"


def test_a_stale_uptime_check_is_caught_too(fake_host, run_ci_watchdog):
    """uptime-check.yml carries the same 60-day disable exposure.

    It is the dead man's switch for the deployment, so its going quiet is
    the same class of invisible fault as the nightly's, one level out.
    """
    serve_runs(fake_host, [run("success", hours_ago=1, run_id=100)])
    fake_host.set_json(
        "/gh/repos/TCKDB/TCKDB/actions/workflows/uptime-check.yml/runs",
        {"workflow_runs": [run("success", hours_ago=48, run_id=300)]},
    )
    proc = run_ci_watchdog(
        env_overrides={
            "TCKDB_WATCHED_WORKFLOWS": f"{WORKFLOW}:30 uptime-check.yml:6",
        }
    )
    assert proc.returncode == 1
    assert any("uptime-check.yml" in t and "has not run" in t for t in titles(fake_host))


# ---------------------------------------------------------------------------
# The workflow wiring is part of the mechanism, so it is asserted too
# ---------------------------------------------------------------------------


def test_the_watchdog_workflow_is_wired_to_the_script_and_the_secret():
    body = (WORKFLOWS_DIR / "ci-watchdog.yml").read_text()
    assert "backend/scripts/ops/tckdb_ci_watchdog.sh" in body
    assert "secrets.TCKDB_NTFY_TOPIC" in body
    assert "schedule:" in body and "workflow_dispatch:" in body
    assert "actions: read" in body, "the run-history API needs it"
    # Without this the state is dropped on exactly the runs that set it --
    # the red ones -- and every red day becomes another identical push.
    save = body.split("Save watchdog state", 1)[1]
    assert "if: always()" in save


def _curl_invocations(text: str) -> list[str]:
    """Every curl command in a file, with line continuations folded in."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return re.findall(r"curl\b[^\n]*", joined)


@pytest.mark.parametrize(
    "path",
    [
        WORKFLOWS_DIR / "uptime-check.yml",
        OPS_DIR / "tckdb_alert_check.sh",
        OPS_DIR / "tckdb_ci_watchdog.sh",
    ],
    ids=lambda p: p.name,
)
def test_every_ntfy_publish_fails_loudly_on_an_http_error(path):
    """Without --fail a 401 or a 5xx is counted as a delivered alert.

    curl exits 0 on an HTTP error unless told otherwise, so the caller
    records "they have been told" when nobody has. That bug was fixed in
    the Pi-side checker and had survived in uptime-check.yml; every place
    that pushes to ntfy is pinned against it here so it cannot come back
    in the next one.
    """
    commands = [c for c in _curl_invocations(path.read_text()) if "ntfy" in c.lower()]
    assert commands, f"{path.name} was expected to publish to ntfy"
    for command in commands:
        assert "--fail" in command, f"{path.name}: {command}"


def test_the_nightly_is_actually_watched():
    """The whole point. A watchdog watching nothing is worse than none."""
    body = (WORKFLOWS_DIR / "ci-watchdog.yml").read_text()
    assert "backend-nightly.yml:" in body
    assert "uptime-check.yml:" in body


def test_the_watched_list_is_a_parseable_spec():
    body = (WORKFLOWS_DIR / "ci-watchdog.yml").read_text()
    line = next(
        stripped
        for raw in body.splitlines()
        if (stripped := raw.strip()).startswith("TCKDB_WATCHED_WORKFLOWS:")
    )
    spec = json.loads(line.split(":", 1)[1].strip())
    for entry in spec.split():
        name, _, hours = entry.rpartition(":")
        assert name.endswith(".yml"), entry
        assert hours.isdigit() and int(hours) > 0, entry
        assert (WORKFLOWS_DIR / name).exists(), f"{name} is watched but does not exist"
