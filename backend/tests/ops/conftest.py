"""Fixtures for exercising the host-ops shell scripts as real processes.

These scripts are the only part of TCKDB that runs *outside* the API, and
they are the part whose failure is hardest to see: a broken checker and a
healthy deployment both produce silence. So they get tested the way they
run -- as a subprocess talking HTTP to a real socket -- rather than by
reading them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

OPS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "ops"
ALERT_CHECK = OPS_DIR / "tckdb_alert_check.sh"

#: Every external binary tckdb_alert_check.sh requires, minus jq. Used to build
#: a PATH on which jq genuinely does not exist. ``hostname`` is deliberately
#: absent: the script already guards it (``hostname || echo "this host"``), and
#: including it would turn a cosmetic gap into a skipped test -- which is how
#: the jq-free path would quietly stop being covered.
_SCRIPT_TOOLS = (
    "bash",
    "cat",
    "curl",
    "date",
    "dirname",
    "grep",
    "head",
    "mkdir",
    "sed",
    "tail",
    "tr",
)


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: str


@dataclass
class FakeHost:
    """A single HTTP server standing in for /status, ntfy and the dead man.

    One server rather than three because the scripts only ever care about
    the URL they were handed, and one socket is one fewer thing to leak.
    """

    port: int
    requests: list[Request] = field(default_factory=list)
    #: Response for GET /status: (http_code, body).
    status_response: tuple[int, str] = (200, "")
    #: Paths that answer 500, used to prove failed publishes are retried.
    failing_paths: set[str] = field(default_factory=set)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def set_status(self, payload: dict, code: int = 200) -> None:
        self.status_response = (code, json.dumps(payload))

    def paths(self, prefix: str) -> list[Request]:
        return [r for r in self.requests if r.path.startswith(prefix)]


@pytest.fixture
def fake_host():
    host = FakeHost(port=0)

    class Handler(BaseHTTPRequestHandler):
        def _record(self, body: str = "") -> None:
            host.requests.append(
                Request(
                    method=self.command,
                    path=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=body,
                )
            )

        def _answer(self) -> None:
            if self.path in host.failing_paths:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"nope")
                return
            if self.path.startswith("/status"):
                code, body = host.status_response
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def do_GET(self):
            self._record()
            self._answer()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self._record(self.rfile.read(length).decode("utf-8", "replace"))
            self._answer()

        def log_message(self, *args):  # silence stderr noise
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def run_alert_check(fake_host, tmp_path):
    """Run tckdb_alert_check.sh against the fake host and return the result."""

    state_file = tmp_path / "alert-state"

    def _run(*, env_overrides: dict | None = None, drop_jq: bool = False):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "TCKDB_STATUS_URL": f"{fake_host.base}/status",
            "TCKDB_NTFY_SERVER": f"{fake_host.base}/ntfy",
            "TCKDB_NTFY_TOPIC": "test-topic",
            "TCKDB_STATE_FILE": str(state_file),
        }
        if drop_jq:
            # A host without jq takes the grep/sed fallback. It is a genuinely
            # different decision path -- and the one that has been wrong before
            # -- so it is exercised rather than assumed equivalent. Building a
            # PATH that holds only the tools the script needs is the only way
            # to make jq reliably absent: `command -v` finds any executable
            # shim, so a stub cannot hide it.
            shim = tmp_path / "nojq-bin"
            shim.mkdir(exist_ok=True)
            for tool in _SCRIPT_TOOLS:
                found = shutil.which(tool)
                if found is None:
                    # Not a skip. A skipped test is silence, and silence
                    # reading as success is the entire subject of this
                    # directory.
                    pytest.fail(
                        f"{tool} is not installed, so the jq-free decision "
                        f"path cannot be exercised on this host"
                    )
                target = shim / tool
                if not target.exists():
                    target.symlink_to(found)
            env["PATH"] = str(shim)
        env.update(env_overrides or {})
        proc = subprocess.run(
            ["bash", str(ALERT_CHECK)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc

    _run.state_file = state_file
    return _run
