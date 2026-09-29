"""The CLI's network commands report an unusable API as one line (#568).

``export`` and ``import --upload`` pointed at a site root rather than its
``/api/v1`` root used to die with ``TypeError: string indices must be
integers``: the web app answered every path with an HTML page and a 200,
and the adapter indexed it. They now print one ``ERROR [...]`` line and
exit 1, with no traceback.

A real local HTTP server rather than a stub client: the defect lived in the
real ``tckdb_client`` response handling, so a stub standing in for it would
test nothing.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tckdb_qcschema.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "energy_v1" / "document.json"
SPA_HTML = b"<!doctype html><html><body><div id=root></div></body></html>"


class _Server:
    def __init__(self, body: bytes, content_type: str) -> None:
        self.body = body
        self.content_type = content_type
        self.requests: list[tuple[str, str]] = []


@pytest.fixture
def serve() -> Iterator:
    servers: list[ThreadingHTTPServer] = []

    def start(body: bytes, content_type: str) -> tuple[str, _Server]:
        state = _Server(body, content_type)

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                state.requests.append((self.command, self.path))
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", state.content_type)
                self.send_header("Content-Length", str(len(state.body)))
                self.end_headers()
                self.wfile.write(state.body)

            do_GET = _answer
            do_POST = _answer

            def log_message(self, *_args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", state

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _one_error_line(stderr: str) -> str:
    assert "Traceback" not in stderr
    lines = [line for line in stderr.splitlines() if line.strip()]
    assert len(lines) == 1, stderr
    return lines[0]


def test_export_against_a_site_root_prints_one_error_line(serve, capsys, monkeypatch):
    monkeypatch.delenv("TCKDB_API_KEY", raising=False)
    site_root, _state = serve(SPA_HTML, "text/html; charset=utf-8")

    rc = main(["export", "calc_abc", "--base-url", site_root])

    captured = capsys.readouterr()
    line = _one_error_line(captured.err)
    assert rc == 1
    assert captured.out == ""
    assert line.startswith("ERROR [TCKDBUnexpectedResponseError]: GET ")
    assert f"{site_root}/scientific/calculations/calc_abc" in line
    assert f"e.g. {site_root}/api/v1 " in line


def test_export_json_mode_reports_the_same_error_as_json(serve, capsys, monkeypatch):
    monkeypatch.delenv("TCKDB_API_KEY", raising=False)
    site_root, _state = serve(SPA_HTML, "text/html")

    rc = main(["export", "calc_abc", "--base-url", site_root, "--json"])

    payload = json.loads(_one_error_line(capsys.readouterr().err))
    assert rc == 1
    assert payload["error_code"] == "TCKDBUnexpectedResponseError"
    assert f"e.g. {site_root}/api/v1 " in payload["message"]


def test_import_upload_against_a_site_root_stops_before_posting(serve, capsys, monkeypatch):
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test_key_value_1234")
    site_root, state = serve(SPA_HTML, "text/html")

    rc = main(
        ["import", str(FIXTURE), "--smiles", "O", "--upload", "--base-url", site_root]
    )

    line = _one_error_line(capsys.readouterr().err)
    assert rc == 1
    assert line.startswith("ERROR [TCKDBUnexpectedResponseError]: GET ")
    # The duplicate pre-check used to read the HTML as "no records" and go
    # on to POST the deposit to the web site. Now nothing is posted.
    assert [method for method, _path in state.requests] == ["GET"]


def test_control_a_real_json_answer_still_reaches_the_adapter(serve, capsys, monkeypatch):
    """JSON from the API is unaffected: the adapter reads it and refuses on
    the data (an ``opt`` record), with its own code and exit status."""

    monkeypatch.delenv("TCKDB_API_KEY", raising=False)
    body = json.dumps({"record": {"calculation": {"type": "opt"}}}).encode()
    api_root, _state = serve(body, "application/json")

    rc = main(["export", "calc_abc", "--base-url", api_root + "/api/v1"])

    line = _one_error_line(capsys.readouterr().err)
    assert rc == 2
    assert line.startswith("REFUSED [export_unsupported_type]:")
