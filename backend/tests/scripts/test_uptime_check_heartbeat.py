"""The uptime probe's three verdicts reach the heartbeat as three different pings.

WHY THIS EXISTS
    ``.github/workflows/uptime-check.yml`` is the only thing that watches the
    deployment from outside the deployment. Its shell body was, until this
    file, executed by nothing but the real schedule -- so a mistake in it
    surfaced as a monitor that had quietly stopped monitoring, which is the
    failure mode the whole workflow exists to prevent. A monitor is exactly
    the code that cannot be left unchecked, because its wrong behaviour looks
    identical to its right behaviour from the outside: silence.

THE PROPERTY
    Three outcomes, three treatments, and the third is the one worth testing:

      the deployment is well          -> ping success
      the deployment answered badly   -> ping /fail
      the deployment did not answer   -> ping NOTHING

    The last is not an oversight and must not be "fixed" into a /fail. A
    GitHub runner with broken egress cannot distinguish a dead Pi from its own
    dead network, so a /fail there reports a deployment outage on no evidence.
    Sending nothing lets the check's own grace period decide: one missed ping
    is absorbed, a persistent inability to look expires it. Same reasoning as
    the API's split between "I could not look" and "your input is bad".

WHAT IS AND IS NOT FAKED
    The workflow's own shell runs, verbatim, extracted from the YAML -- so
    this tests the shipped text and not a copy of it. Only the two endpoints
    are local: a stand-in /status that can be told what to answer, and a
    stand-in healthchecks.io that records the paths it was pinged on. The
    assertions are on that recording, including its emptiness.

    Deliberately asserts on what was NOT sent. A test that only checked
    "success pings /hb" passes against an implementation that pings /hb every
    single run, which would report a dead deployment as healthy forever.
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
            "components": {
                "artifact_storage": {"healthy": False, "reason": "no room"}
            },
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

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            if self.path.startswith("/hb"):
                hits.append(self.path)
                return self._json(200, {"ok": True})
            if self.path == "/status":
                code, payload = _BODIES[mode]
                return self._json(code, payload)
            self._json(404, {"detail": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - the ntfy stand-in
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


def _run(probe: Path, mode: str, *, reachable: bool) -> list[str]:
    """Run the probe against a stand-in deployment; return the pings it sent."""
    hits: list[str] = []
    server = HTTPServer(("127.0.0.1", 0), _handler_for(mode, hits))
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # An unreachable deployment is a port with nothing on it. The heartbeat
        # endpoint stays up, so "sent nothing" cannot be confused with "could
        # not have sent anything".
        status_port = port if reachable else _closed_port()
        subprocess.run(
            ["bash", str(probe)],
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "STATUS_URL": f"http://127.0.0.1:{status_port}/status",
                "NTFY_TOPIC": "",
                "HEARTBEAT_URL": f"http://127.0.0.1:{port}/hb",
            },
            capture_output=True,
            timeout=120,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    return hits


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


def test_a_well_deployment_pings_success(probe_script) -> None:
    assert _run(probe_script, "ok", reachable=True) == ["/hb"]


@pytest.mark.parametrize("mode", ["degraded", "contradictory", "http500"])
def test_a_deployment_that_answered_badly_pings_fail(probe_script, mode) -> None:
    """It answered, and the answer was bad. That is evidence; report it."""
    assert _run(probe_script, mode, reachable=True) == ["/hb/fail"]


def test_an_unreachable_deployment_pings_nothing_at_all(probe_script) -> None:
    """The assertion the design rests on.

    Without it, the correct implementation and one that reports every failure
    as a deployment outage are indistinguishable -- to this suite and to a
    reviewer reading it.

    Mutation-checked: forcing the "did it answer" flag true makes this the only
    failing case, and removing the success ping makes
    ``test_a_well_deployment_pings_success`` the only failing case.
    """
    assert _run(probe_script, "ok", reachable=False) == [], (
        "the probe reported a verdict on a deployment it could not reach; a "
        "runner with broken networking would page as a deployment outage"
    )
