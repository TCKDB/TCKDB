"""The CLI's scan branches, and the missing-base-URL error line.

``import --upload`` for a torsion drive runs against a real local HTTP
server through the real ``tckdb_client``: the requests it records are
exactly what the adapter sent.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tckdb_qcschema.cli import main
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.uploader import scan_bundle_idempotency_key

FIXTURES = Path(__file__).parent / "fixtures"
TD = FIXTURES / "torsiondrive_v2" / "document.json"
PARENT = FIXTURES / "torsiondrive_parent_opt_v2" / "document.json"
ENERGY = FIXTURES / "energy_v1" / "document.json"


def _one_line(stderr: str) -> str:
    assert "Traceback" not in stderr
    lines = [line for line in stderr.splitlines() if line.strip()]
    assert len(lines) == 1, stderr
    return lines[0]


def test_report_scan(capsys) -> None:
    rc = main(["report", str(TD), "--parent-opt", str(PARENT), "--smiles", "OO", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["calculation_type"] == "scan"
    assert (out["dimension"], out["point_count"]) == (1, 4)
    assert out["record_kind"] == "torsion_drive"
    assert any(e.startswith("scan_results") for e in out["mapping_report"]["retained_only"])


def test_scan_without_parent_is_refused(capsys) -> None:
    rc = main(["report", str(TD), "--smiles", "OO"])
    assert rc == 2
    assert _one_line(capsys.readouterr().err).startswith("REFUSED [scan_parent_opt_required]:")


def test_parent_opt_on_a_non_drive_is_refused_not_ignored(capsys) -> None:
    rc = main(["import", str(ENERGY), "--smiles", "O", "--parent-opt", str(PARENT)])
    assert rc == 2
    assert _one_line(capsys.readouterr().err).startswith("REFUSED [scan_parent_mismatch]:")


def test_scan_dry_run_names_the_bundle_key(capsys) -> None:
    rc = main(["import", str(TD), "--parent-opt", str(PARENT), "--smiles", "OO", "--dry-run", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    expected = scan_bundle_idempotency_key(
        read_document(TD.read_bytes()).canonical_sha256,
        read_document(PARENT.read_bytes()).canonical_sha256,
    )
    assert out == {"computed_species_idempotency_key": expected, "calculation_type": "scan"}


@pytest.mark.parametrize(
    "argv",
    [
        ["export", "calc_abc"],
        ["import", str(ENERGY), "--smiles", "O", "--upload"],
        ["import", str(TD), "--parent-opt", str(PARENT), "--smiles", "OO", "--upload"],
    ],
    ids=["export", "import-upload", "scan-import-upload"],
)
def test_missing_base_url_is_one_error_line(argv, capsys, monkeypatch) -> None:
    monkeypatch.delenv("TCKDB_BASE_URL", raising=False)
    rc = main(argv)
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert _one_line(captured.err).startswith("ERROR [missing_base_url]:")


@pytest.fixture
def api() -> Iterator[tuple[str, list]]:
    requests: list[tuple[str, str, dict | None, str | None]] = []
    response = {
        "species_entry_id": 1,
        "submission_id": 1,
        "conformers": [
            {
                "key": "qcschema_conformer",
                "primary_calculation": {"key": "qcschema_opt", "calculation_id": 11, "type": "opt", "role": "primary"},
                "additional_calculations": [
                    {"key": "qcschema_scan", "calculation_id": 12, "type": "scan", "role": "additional"}
                ],
            }
        ],
    }

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, body: dict, status: int = 200) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            requests.append(("GET", self.path, None, None))
            self._reply({"records": []})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length))
            requests.append(("POST", self.path, body, self.headers.get("Idempotency-Key")))
            self._reply(response, 201)

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/api/v1", requests
    server.shutdown()
    server.server_close()


def test_scan_upload_posts_one_bundle(api, capsys, monkeypatch) -> None:
    base_url, requests = api
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test_key_value_1234")
    rc = main(
        ["import", str(TD), "--parent-opt", str(PARENT), "--smiles", "OO", "--upload", "--base-url", base_url, "--json"]
    )
    assert rc == 0, capsys.readouterr().err
    assert json.loads(capsys.readouterr().out) == {
        "scan_calculation_id": 12,
        "opt_calculation_id": 11,
        "replayed": False,
    }
    gets = [r for r in requests if r[0] == "GET"]
    posts = [r for r in requests if r[0] == "POST"]
    # A duplicate pre-check for each of the two raw documents, then one POST.
    assert len(gets) == 2 and all(r[1].startswith("/api/v1/scientific/artifacts/search") for r in gets)
    assert len(posts) == 1
    _method, path, body, key = posts[0]
    assert path == "/api/v1/uploads/computed-species"
    assert key.endswith(":computed-species")
    (scan_calc,) = body["conformers"][0]["additional_calculations"]
    assert len(scan_calc["scan_result"]["points"]) == 4
