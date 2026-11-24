"""A local stub of an OpenAI-compatible ``/v1/chat/completions`` endpoint for provider tests.

Serves a script of responses in order (the last one repeats) and records every request it receives.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class StubResponse:
    body: Any = None
    status: int = 200
    delay: float = 0.0


@dataclass
class RecordedRequest:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class StubServer:
    script: list[StubResponse]
    requests: list[RecordedRequest] = field(default_factory=list)
    _httpd: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._httpd is not None
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"

    def start(self) -> "StubServer":
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                index = len(stub.requests)
                stub.requests.append(
                    RecordedRequest(self.path, {k.lower(): v for k, v in self.headers.items()}, json.loads(raw))
                )
                step = stub.script[min(index, len(stub.script) - 1)]
                if step.delay:
                    time.sleep(step.delay)
                payload = json.dumps(step.body if step.body is not None else {}).encode()
                try:
                    self.send_response(step.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args: Any) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()


def refused_base_url() -> str:
    """A base URL on a port that was free a moment ago and has nothing listening."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/v1"
