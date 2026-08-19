"""The uptime probe's verdict reaches the heartbeat as the right kind of ping.

WHY THIS EXISTS
    ``.github/workflows/uptime-check.yml`` is the only thing that watches the
    deployment from outside the deployment. Its shell body was, until this
    file, executed by nothing but the real schedule -- so a mistake in it
    surfaced as a monitor that had quietly stopped monitoring, which is the
    failure mode the whole workflow exists to prevent. A monitor is exactly
    the code that cannot be left unchecked, because its wrong behaviour looks
    identical to its right behaviour from the outside: silence.

THE PROPERTY
    Two outcomes:

      the deployment is well  -> ping success
      anything else           -> ping /fail, INCLUDING "it did not answer"

    An earlier version of this file asserted that an unreachable deployment
    must ping nothing, to avoid reporting an outage when the runner's own
    networking was the broken thing. That is wrong, and the reason is worth
    keeping: the /fail ping travels the same egress. A runner that cannot
    reach the deployment for its own network reasons cannot reach
    healthchecks.io either, so the check falls silent by itself. The guard
    prevented nothing and delayed every real outage by a period plus a grace.

    Two consequences this file pins:

      * delivering /fail is itself evidence the egress works, so the
        deployment really is what cannot be reached -- and unreachable is
        down, whatever the remote process table says;
      * a heartbeat that cannot be delivered must not change the job's own
        verdict, so a healthchecks.io outage does not redden a healthy run.
        See ``test_an_unreachable_heartbeat_does_not_redden_a_healthy_run``
        for what actually enforces that, which is not what it looks like.

WHAT IS AND IS NOT FAKED
    The workflow's own shell runs, verbatim, extracted from the YAML -- so
    this tests the shipped text and not a copy of it. Only the two endpoints
    are local: a stand-in /status that can be told what to answer, and a
    stand-in healthchecks.io that records the paths it was pinged on. The
    assertions are on that recording, including its emptiness.

    Deliberately asserts on what was NOT sent. A test that only checked
    "success pings /hb" passes against an implementation that pings /hb every
    single run, which would report a dead deployment as healthy forever -- so
    every case asserts the exact list of pings, never merely that one is
    present.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "uptime-check.yml"

# What the stand-in deployment answers, keyed by the case under test.
_BODIES = {
    "ok": (200, {"status": "ok", "degraded": [], "components": {}}),
    "degraded": (
        200,
        {
            "status": "degraded",
            "degraded": ["artifact_storage"],
            "components": {"artifact_storage": {"healthy": False, "reason": "no room"}},
        },
    ),
    # status says ok while the components disagree. The workflow treats the
    # components as authoritative; so does this.
    "contradictory": (
        200,
        {
            "status": "ok",
            "degraded": ["database"],
            "components": {"database": {"healthy": False, "reason": "drift"}},
        },
    ),
    "http500": (500, {"detail": "the API is failing before it can answer"}),
}


def _handler_for(mode: str, hits: list[str]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep pytest output readable
            pass

        def _json(self, code: int, payload) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.startswith("/hb"):
                hits.append(self.path)
                return self._json(200, {"ok": True})
            if self.path == "/status":
                code, payload = _BODIES[mode]
                return self._json(code, payload)
            self._json(404, {"detail": "not found"})

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self._json(200, {"ok": True})

    return Handler


@pytest.fixture(scope="module")
def probe_script(tmp_path_factory) -> Path:
    """The workflow's own run: block, extracted and made executable.

    Reads the shipped YAML so the thing under test is the thing that runs in
    CI. If the step is ever renamed or reordered this raises rather than
    silently testing an empty string.
    """
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["probe"]["steps"]
    bodies = [step["run"] for step in steps if "run" in step]
    assert len(bodies) == 1, f"expected exactly one run: block, found {len(bodies)}"
    script = bodies[0]
    assert "heartbeat" in script, "the probe no longer mentions a heartbeat"

    path = tmp_path_factory.mktemp("probe") / "probe.sh"
    path.write_text(script, encoding="utf-8")
    return path


def _run(
    probe: Path,
    mode: str,
    *,
    reachable: bool = True,
    heartbeat_reachable: bool = True,
) -> tuple[list[str], int]:
    """Run the probe against stand-ins; return the pings sent and the exit code.

    ``heartbeat_reachable=False`` models the case the design turns on: this
    runner's egress is broken, so neither endpoint can be reached.
    """
    hits: list[str] = []
    server = HTTPServer(("127.0.0.1", 0), _handler_for(mode, hits))
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # An unreachable endpoint is a port with nothing on it.
        status_port = port if reachable else _closed_port()
        hb_port = port if heartbeat_reachable else _closed_port()
        completed = subprocess.run(
            ["bash", str(probe)],
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "STATUS_URL": f"http://127.0.0.1:{status_port}/status",
                "NTFY_TOPIC": "",
                "HEARTBEAT_URL": f"http://127.0.0.1:{hb_port}/hb",
            },
            capture_output=True,
            timeout=120,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    return hits, completed.returncode


def _closed_port() -> int:
    """A port nothing is listening on: bind, read the number, release it."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module", autouse=True)
def _require_tools() -> None:
    """Fail loudly rather than skip.

    A skipped monitor test is a green tick over an unexercised monitor, which
    is the defect this file is about. bash, curl and jq are present on the
    ubuntu-latest runner this gate uses.
    """
    missing = [tool for tool in ("bash", "curl", "jq") if shutil.which(tool) is None]
    assert not missing, f"cannot exercise the probe without: {', '.join(missing)}"


def test_a_well_deployment_pings_success_and_only_success(probe_script) -> None:
    hits, code = _run(probe_script, "ok")
    assert hits == ["/hb"]
    assert code == 0


@pytest.mark.parametrize("mode", ["degraded", "contradictory", "http500"])
def test_a_deployment_that_answered_badly_pings_fail(probe_script, mode) -> None:
    """It answered, and the answer was bad. That is evidence; report it."""
    hits, code = _run(probe_script, mode)
    assert hits == ["/hb/fail"]
    assert code == 1


def test_an_unreachable_deployment_pings_fail(probe_script) -> None:
    """Unreachable is down.

    The heartbeat endpoint is up in this case while the deployment is not,
    which is the real-world shape of "GitHub's network is fine, the Pi is
    not". Reaching one and not the other is what makes the /fail meaningful
    rather than a guess.

    Mutation-checked: removing the ``heartbeat "/fail"`` on the failure path
    makes this and the three answered-badly cases fail, and nothing else.
    """
    hits, code = _run(probe_script, "ok", reachable=False)
    assert hits == ["/hb/fail"]
    assert code == 1


def test_a_runner_that_can_reach_nothing_stays_silent(probe_script) -> None:
    """The case that made the explicit "say nothing" branch unnecessary.

    When this runner's own egress is broken, neither endpoint answers, so the
    /fail cannot be delivered and the check falls silent on its own -- exactly
    what the removed branch was written to achieve, achieved by the topology
    instead, and without delaying a real outage by a period plus a grace.
    """
    hits, _ = _run(probe_script, "ok", reachable=False, heartbeat_reachable=False)
    assert hits == []


def test_an_unreachable_heartbeat_does_not_redden_a_healthy_run(
    probe_script,
) -> None:
    """The deployment is well; healthchecks.io is the thing that is down.

    That must stay a green run. A monitoring dependency failing is not a
    deployment failure, and turning it into one trains an operator to ignore
    the workflow -- which costs more than the heartbeat was worth.

    ON WHAT ENFORCES THIS, measured rather than assumed, because the obvious
    answer is wrong in both directions:

    * TODAY, nothing about the ping can change the exit code at all. Both
      call sites are followed by an unconditional ``exit`` and the script uses
      ``set -uo pipefail`` without ``-e``. Confirmed by making ``heartbeat``
      return curl's status: every test here still passed. So the ``|| echo``
      is currently a log line, not a guard.
    * It BECOMES the guard the moment ``-e`` is added, since a command on the
      left of ``||`` is exempt from ``set -e`` while a bare one is not.

    Mutating ``set -uo`` to ``set -euo`` is caught -- but by
    ``test_an_unreachable_deployment_pings_fail``, not by this test: under
    ``-e`` the run aborts at the failed status curl and never reaches the
    ping at all. This test stays green there precisely because ``|| echo``
    does its job. Both are kept: they fail for different reasons, and the
    pair is what says which mechanism is doing the work.
    """
    hits, code = _run(probe_script, "ok", heartbeat_reachable=False)
    assert hits == []
    assert code == 0, "an unreachable heartbeat turned a healthy deployment into a failed workflow run"
