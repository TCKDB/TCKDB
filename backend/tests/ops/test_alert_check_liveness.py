"""The health checker has to be able to report its own death.

A checker that only proves the happy path proves nothing worth having: a
dead checker and a healthy deployment are the same observation from a
phone, which is silence. These tests are about the *unhappy* paths --
what reaches the outside world when the checker is broken, when the
deployment is broken, and when the notification channel is broken.

The single load-bearing rule they encode:

    The dead man's ping means "this script ran", never "TCKDB is well".

Both halves matter and each has been got wrong in the wild. Ping only on
a healthy verdict and a degraded component silences the host heartbeat,
so the external service pages "the Pi is gone" while the Pi is busy
telling you exactly which component failed. Ping unconditionally --
including from an aborted run -- and a checker that cannot start still
looks alive, which is the defect these tests exist to prevent.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess

import pytest

from tests.ops.conftest import OPS_DIR

HEALTHY = {
    "status": "ok",
    "degraded": [],
    "components": {
        "database": {"healthy": True, "alembic_revision": "abc123", "reason": None},
        "worker": {"healthy": True, "reason": None},
        "artifact_storage": {
            "healthy": True,
            "reachable": True,
            "endpoint": "http://minio:9000",
            "bucket": "tckdb-artifacts",
            "reason": None,
        },
    },
}

DEGRADED = {
    "status": "degraded",
    "degraded": ["artifact_storage"],
    "components": {
        "database": {"healthy": True, "alembic_revision": "abc123", "reason": None},
        "worker": {"healthy": True, "reason": None},
        "artifact_storage": {
            "healthy": False,
            "reachable": False,
            "endpoint": "http://127.0.0.1:9000",
            "bucket": "tckdb-artifacts",
            "reason": "cannot reach object store (EndpointConnectionError)",
        },
    },
}


def deadman_pings(fake_host):
    return fake_host.paths("/deadman")


def ntfy_pushes(fake_host):
    return fake_host.paths("/ntfy")


# ---------------------------------------------------------------------------
# The ping asserts the checker ran, not that the system is well
# ---------------------------------------------------------------------------


def test_deadman_pinged_on_healthy_run(fake_host, run_alert_check):
    fake_host.set_status(HEALTHY)
    proc = run_alert_check(
        env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"}
    )
    assert proc.returncode == 0, proc.stderr
    assert len(deadman_pings(fake_host)) == 1


def test_deadman_pinged_when_deployment_is_degraded(fake_host, run_alert_check):
    """A broken component must not silence the checker's own heartbeat.

    This is the reason the ping is not tied to the verdict. If it were,
    an artifact-storage outage would stop the pings, the external dead
    man would fire "host is gone", and an operator would go looking for
    a dead Raspberry Pi while the Pi was pushing an accurate description
    of the real fault to their phone.
    """
    fake_host.set_status(DEGRADED)
    proc = run_alert_check(
        env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"}
    )
    assert proc.returncode == 1, "a degraded deployment still exits non-zero"
    assert len(deadman_pings(fake_host)) == 1, (
        "the checker ran to a verdict, so it must assert its own liveness"
    )
    assert len(ntfy_pushes(fake_host)) == 1, "and it must still alert on the fault"


def test_deadman_pinged_when_the_api_is_unreachable(fake_host, run_alert_check, tmp_path):
    """The API being down is a verdict, not a failure of the checker."""
    fake_host.set_status(HEALTHY)
    proc = run_alert_check(
        env_overrides={
            # A port nothing is listening on: curl fails, the script decides
            # "unreachable", which is a completed run.
            "TCKDB_STATUS_URL": "http://127.0.0.1:1/status",
            "TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman",
        }
    )
    assert proc.returncode == 1
    assert len(deadman_pings(fake_host)) == 1
    assert "unreachable" in proc.stdout


def test_deadman_pinged_when_status_endpoint_is_missing(fake_host, run_alert_check):
    """A 404 (an old build deployed) is also a completed run."""
    fake_host.set_status({"detail": "Not Found"}, code=404)
    proc = run_alert_check(
        env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"}
    )
    assert proc.returncode == 1
    assert len(deadman_pings(fake_host)) == 1
    assert "bad_endpoint" in proc.stdout


# ---------------------------------------------------------------------------
# ... and the checker's own death is silence, deliberately
# ---------------------------------------------------------------------------


def test_a_checker_that_cannot_start_sends_no_ping(fake_host, run_alert_check):
    """The defect this whole file exists for.

    An unset topic is the most common way this script dies on a fresh
    host, and it dies *before* it can conclude anything. It must not
    ping: the external dead man's switch converts that silence into an
    alert, and a ping here would forge a liveness signal for a checker
    that never checked anything.
    """
    fake_host.set_status(HEALTHY)
    proc = run_alert_check(
        env_overrides={
            "TCKDB_NTFY_TOPIC": "",
            "TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman",
        }
    )
    assert proc.returncode == 2
    assert deadman_pings(fake_host) == [], (
        "a checker that refused to run must stay silent so its silence is the alarm"
    )


def test_ping_failure_is_reported_rather_than_swallowed(fake_host, run_alert_check):
    """If even the heartbeat cannot be sent, say so in the journal.

    The run still succeeds -- the deployment is fine and that is what the
    exit code is for -- but a heartbeat that silently never leaves the
    host is the failure mode in miniature.
    """
    fake_host.set_status(HEALTHY)
    proc = run_alert_check(
        env_overrides={"TCKDB_DEADMAN_URL": "http://127.0.0.1:1/deadman"}
    )
    assert proc.returncode == 0
    assert "dead man's switch ping" in proc.stderr


# ---------------------------------------------------------------------------
# An unmonitored monitor announces itself, once
# ---------------------------------------------------------------------------


def test_missing_deadman_url_is_announced_exactly_once(fake_host, run_alert_check):
    fake_host.set_status(HEALTHY)

    first = run_alert_check()
    assert first.returncode == 0
    notices = [r for r in ntfy_pushes(fake_host) if "dead man" in r.headers.get("title", "")]
    assert len(notices) == 1, "the gap must be reported"

    second = run_alert_check()
    assert second.returncode == 0
    notices = [r for r in ntfy_pushes(fake_host) if "dead man" in r.headers.get("title", "")]
    assert len(notices) == 1, "and reported once, not every five minutes"


def test_no_such_notice_when_a_deadman_is_configured(fake_host, run_alert_check):
    fake_host.set_status(HEALTHY)
    run_alert_check(env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"})
    notices = [r for r in ntfy_pushes(fake_host) if "dead man" in r.headers.get("title", "")]
    assert notices == []


# ---------------------------------------------------------------------------
# A push that never arrived must not be recorded as delivered
# ---------------------------------------------------------------------------


def test_failed_push_is_retried_on_the_next_run(fake_host, run_alert_check):
    """State advances on delivery, not on intent.

    The edge-triggered design means a missed transition is missed
    forever: mark "degraded" as announced when the announcement failed
    and the deployment stays broken and quiet until it changes state
    again. So the state file only moves once ntfy has accepted the push.
    """
    fake_host.set_status(HEALTHY)
    run_alert_check(env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"})

    fake_host.set_status(DEGRADED)
    fake_host.failing_paths.add("/ntfy/test-topic")
    first = run_alert_check(
        env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"}
    )
    assert first.returncode == 1
    assert "state not advanced" in first.stderr
    assert run_alert_check.state_file.read_text() == "ok", (
        "an undelivered alert must not be recorded as delivered"
    )

    fake_host.failing_paths.clear()
    second = run_alert_check(
        env_overrides={"TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman"}
    )
    assert second.returncode == 1
    assert run_alert_check.state_file.read_text() == "degraded"
    delivered = [
        r for r in ntfy_pushes(fake_host) if "degraded" in r.headers.get("title", "").lower()
    ]
    assert len(delivered) == 2, "one failed attempt, one successful retry"


# ---------------------------------------------------------------------------
# The systemd wiring is part of the mechanism, so it is asserted too
# ---------------------------------------------------------------------------


def test_the_unit_alerts_when_the_checker_itself_fails():
    """Covers the deaths that happen before the script can ping anything.

    A moved repo, a missing EnvironmentFile, a syntax error: the script
    never runs, so no ping is even attempted, and only systemd knows.
    OnFailure= turns that red unit into a push.

    Reading the unit is all this test does, which is exactly how three
    separate silent failures survived in it. The tests below run its
    ExecStart as a process instead; this one only pins the wiring that no
    process can show.
    """
    unit = (OPS_DIR / "tckdb-alert.service").read_text()
    assert "OnFailure=tckdb-alert-failed.service" in unit
    failure_unit = OPS_DIR / "tckdb-alert-failed.service"
    assert failure_unit.exists()
    body = failure_unit.read_text()
    assert "TCKDB_NTFY_TOPIC" in body
    assert "ExecStart=" in body


def test_the_onfailure_unit_survives_a_missing_environment_file():
    """The leading `-` is not decoration; systemd requires it.

    Without it systemd refuses to START the unit when the file is absent,
    and "the EnvironmentFile is missing" is one of the failures of
    tckdb-alert.service this unit is supposed to report. Both units then
    die of the same cause and nothing is pushed -- the alerting path
    failing for precisely the reason it was needed.

    This is asserted by reading, because systemd is not available to the
    test suite. It was verified by running the shipped unit under
    `systemd-run --user`: with `EnvironmentFile=/absent` the command never
    executes (Result=resources), with `EnvironmentFile=-/absent` it runs
    and exits 78/CONFIG with the reason in the journal.
    """
    body = (OPS_DIR / "tckdb-alert-failed.service").read_text()
    line = next(
        stripped
        for raw in body.splitlines()
        if (stripped := raw.strip()).startswith("EnvironmentFile=")
    )
    assert line.startswith("EnvironmentFile=-"), (
        "an OnFailure unit that cannot start without its env file cannot "
        "report a missing env file"
    )


def test_the_checker_unit_requires_its_environment_file():
    """And the main unit deliberately does not carry the `-`.

    It cannot check anything without the topic, so a missing file should
    stop it and fire OnFailure=. The asymmetry between the two units is
    the mechanism, not an oversight, so it is pinned in both directions.
    """
    body = (OPS_DIR / "tckdb-alert.service").read_text()
    line = next(
        stripped
        for raw in body.splitlines()
        if (stripped := raw.strip()).startswith("EnvironmentFile=")
    )
    assert not line.startswith("EnvironmentFile=-")


def _exec_start(unit_name: str) -> list[str]:
    """The unit's ExecStart as an argv, with systemd's line folding applied.

    systemd joins `\\`-continued lines with whitespace and then splits the
    result the way a shell would; shlex is close enough for a command line
    that is one program and one single-quoted argument.
    """
    text = (OPS_DIR / unit_name).read_text()
    text = re.sub(r"\\\n\s*", " ", text)
    line = next(
        stripped
        for raw in text.splitlines()
        if (stripped := raw.strip()).startswith("ExecStart=")
    )
    return shlex.split(line.split("=", 1)[1])


def _run_exec_start(unit_name: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        _exec_start(unit_name),
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_onfailure_push_actually_leaves_the_host(fake_host):
    """The happy path of the alerting path, executed rather than read.

    Nobody had ever seen this command run. It is the only thing that
    speaks when the checker cannot, so it is run here against a socket
    that records what arrived.
    """
    proc = _run_exec_start(
        "tckdb-alert-failed.service",
        {
            "TCKDB_NTFY_SERVER": f"{fake_host.base}/ntfy",
            "TCKDB_NTFY_TOPIC": "test-topic",
        },
    )
    assert proc.returncode == 0, proc.stderr
    sent = fake_host.paths("/ntfy")
    assert len(sent) == 1
    assert sent[0].path == "/ntfy/test-topic"
    assert "FAILED" in sent[0].headers.get("title", "")
    assert "tckdb-alert.service" in sent[0].body, "name the unit that died"
    assert proc.stdout == "", (
        "ntfy's publish response echoes the topic back, and this command's "
        "stdout is the journal; the topic is password-equivalent, so the "
        "response body is discarded rather than logged"
    )


def test_an_unset_topic_alerts_nobody_and_says_so(fake_host):
    """The second silent failure: a push to ntfy.sh/ with no topic.

    An empty TCKDB_NTFY_TOPIC left the URL ending in a slash. ntfy answers
    it, curl was satisfied, the unit went green, and the alert reached
    nobody -- the alerting path reporting success for a delivery it had
    not made. There is no fallback topic to use instead, so the only
    honest outcome is to refuse loudly.
    """
    proc = _run_exec_start(
        "tckdb-alert-failed.service",
        {"TCKDB_NTFY_SERVER": f"{fake_host.base}/ntfy", "TCKDB_NTFY_TOPIC": ""},
    )
    assert proc.returncode != 0, "a push that reached nobody is not a success"
    assert fake_host.paths("/ntfy") == [], "and nothing was posted to no topic"
    assert "TCKDB_NTFY_TOPIC" in proc.stderr, "the journal has to say why"


def test_the_onfailure_push_fails_loudly_on_an_http_error(fake_host):
    """A 5xx from ntfy is not a delivered alert.

    curl exits 0 on an HTTP error unless told otherwise; the same bug has
    been fixed in tckdb_alert_check.sh and in uptime-check.yml, and this
    is the third place it lived.
    """
    fake_host.failing_paths.add("/ntfy/test-topic")
    proc = _run_exec_start(
        "tckdb-alert-failed.service",
        {
            "TCKDB_NTFY_SERVER": f"{fake_host.base}/ntfy",
            "TCKDB_NTFY_TOPIC": "test-topic",
        },
    )
    assert proc.returncode != 0
    assert fake_host.paths("/ntfy"), "it was attempted"


def test_a_checker_that_dies_before_a_verdict_does_not_exit_one(tmp_path, fake_host):
    """The third silent failure: SuccessExitStatus=0 1 masking a crash.

    bash exits 1 on an unbound variable under `set -u`, which is the same
    code this script uses for "the deployment is unhealthy" -- and the
    unit tells systemd that 1 is a success, so OnFailure= never fired for
    a checker that died partway through. A dead checker and a healthy
    deployment are the same observation from a phone.

    The bug is injected into a copy rather than waited for: a real one
    would be a line somebody adds later, and the guard has to hold for a
    line nobody has written yet.
    """
    mutated = tmp_path / "crashing_alert_check.sh"
    original = (OPS_DIR / "tckdb_alert_check.sh").read_text()
    marker = 'raw="$(curl'
    assert marker in original, "the script no longer has the line this test cuts at"
    mutated.write_text(original.replace(marker, 'echo "${a_typo_nobody_caught}"\n' + marker, 1))

    proc = subprocess.run(
        ["bash", str(mutated)],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "TCKDB_STATUS_URL": f"{fake_host.base}/status",
            "TCKDB_NTFY_SERVER": f"{fake_host.base}/ntfy",
            "TCKDB_NTFY_TOPIC": "test-topic",
            "TCKDB_STATE_FILE": str(tmp_path / "state"),
            "TCKDB_DEADMAN_URL": f"{fake_host.base}/deadman",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2, (
        "an exit the unit lists as a success is how a dead checker stays "
        "invisible; a death before a verdict must not be reported as one"
    )
    assert deadman_pings(fake_host) == [], "and it did not reach a verdict to ping for"
    unit = (OPS_DIR / "tckdb-alert.service").read_text()
    success_line = next(
        stripped
        for raw in unit.splitlines()
        if (stripped := raw.strip()).startswith("SuccessExitStatus=")
    )
    assert str(proc.returncode) not in success_line.split("=", 1)[1].split(), (
        "the code the checker dies with must not be one systemd calls success"
    )


@pytest.mark.parametrize(
    "unit_name",
    ["tckdb-alert.service", "tckdb-alert-failed.service", "tckdb-alert.timer"],
)
def test_units_are_present_and_non_empty(unit_name):
    path = OPS_DIR / unit_name
    assert path.exists(), f"{unit_name} is part of the alerting mechanism"
    assert path.read_text().strip()
