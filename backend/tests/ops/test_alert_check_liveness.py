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
    """
    unit = (OPS_DIR / "tckdb-alert.service").read_text()
    assert "OnFailure=tckdb-alert-failed.service" in unit
    failure_unit = OPS_DIR / "tckdb-alert-failed.service"
    assert failure_unit.exists()
    body = failure_unit.read_text()
    assert "TCKDB_NTFY_TOPIC" in body
    assert "ExecStart=" in body


@pytest.mark.parametrize(
    "unit_name",
    ["tckdb-alert.service", "tckdb-alert-failed.service", "tckdb-alert.timer"],
)
def test_units_are_present_and_non_empty(unit_name):
    path = OPS_DIR / unit_name
    assert path.exists(), f"{unit_name} is part of the alerting mechanism"
    assert path.read_text().strip()
