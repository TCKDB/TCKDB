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
from urllib.parse import parse_qs, urlsplit

import pytest

from tckdb_qcschema.cli import main
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.uploader import scan_bundle_idempotency_key, sha256_bytes

FIXTURES = Path(__file__).parent / "fixtures"
TD = FIXTURES / "torsiondrive_v2" / "document.json"
PARENT = FIXTURES / "torsiondrive_parent_opt_v2" / "document.json"
ENERGY = FIXTURES / "energy_v1" / "document.json"
ROTOR1 = FIXTURES / "torsiondrive_rotor1_v2" / "document.json"
ROTOR2 = FIXTURES / "torsiondrive_rotor2_v2" / "document.json"
PARENT_3 = FIXTURES / "torsiondrive_2d_parent_opt_v2" / "document.json"


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
    assert out == {"computed_species_idempotency_key": expected, "calculation_type": "scan", "drive_count": 1}


def test_two_rotor_dry_run_is_one_bundle_over_all_three_documents(capsys) -> None:
    rc = main(
        ["import", str(ROTOR1), str(ROTOR2), "--parent-opt", str(PARENT_3), "--smiles", "OOO", "--dry-run", "--json"]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    expected = scan_bundle_idempotency_key(
        read_document(ROTOR1.read_bytes()).canonical_sha256,
        read_document(PARENT_3.read_bytes()).canonical_sha256,
        additional_canonical_sha256s=[read_document(ROTOR2.read_bytes()).canonical_sha256],
    )
    assert out == {"computed_species_idempotency_key": expected, "calculation_type": "scan", "drive_count": 2}


def test_several_documents_that_are_not_drives_are_refused(capsys) -> None:
    rc = main(["report", str(ENERGY), str(ENERGY), "--smiles", "O"])
    assert rc == 2
    assert _one_line(capsys.readouterr().err).startswith("REFUSED [multiple_documents_unsupported]:")


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
def api() -> Iterator[tuple[str, list, set]]:
    """A local API: the artifact search finds any sha256 in ``deposited``;
    a bundle POST answers with ids 11 (the opt), 12, 13, ... (the scans)."""
    requests: list[tuple[str, str, dict | None, str | None]] = []
    deposited: set[str] = set()

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
            sha = parse_qs(urlsplit(self.path).query).get("sha256", [""])[0]
            records = [{"calculation": {"calculation_id": 7}}] if sha in deposited else []
            self._reply({"records": records})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length))
            requests.append(("POST", self.path, body, self.headers.get("Idempotency-Key")))
            conformer = body["conformers"][0]
            self._reply(
                {
                    "species_entry_id": 1,
                    "submission_id": 1,
                    "conformers": [
                        {
                            "key": conformer["key"],
                            "primary_calculation": {
                                "key": conformer["primary_calculation"]["key"],
                                "calculation_id": 11,
                                "type": "opt",
                                "role": "primary",
                            },
                            "additional_calculations": [
                                {"key": c["key"], "calculation_id": 12 + i, "type": "scan", "role": "additional"}
                                for i, c in enumerate(conformer["additional_calculations"])
                            ],
                        }
                    ],
                },
                201,
            )

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/api/v1", requests, deposited
    server.shutdown()
    server.server_close()


def test_scan_upload_posts_one_bundle(api, capsys, monkeypatch) -> None:
    base_url, requests, _deposited = api
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test_key_value_1234")
    rc = main(
        ["import", str(TD), "--parent-opt", str(PARENT), "--smiles", "OO", "--upload", "--base-url", base_url, "--json"]
    )
    assert rc == 0, capsys.readouterr().err
    assert json.loads(capsys.readouterr().out) == {
        "scan_calculation_ids": [12],
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


def test_two_rotors_go_up_in_one_bundle_with_one_opt(api, capsys, monkeypatch) -> None:
    """Review finding: a second rotor imported on its own posts its parent
    again. Imported together, the parent is one opt and each rotor a scan
    hanging off it."""
    base_url, requests, _deposited = api
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test_key_value_1234")
    rc = main(
        ["import", str(ROTOR1), str(ROTOR2), "--parent-opt", str(PARENT_3), "--smiles", "OOO",
         "--upload", "--base-url", base_url, "--json"]
    )
    assert rc == 0, capsys.readouterr().err
    assert json.loads(capsys.readouterr().out) == {
        "scan_calculation_ids": [12, 13],
        "opt_calculation_id": 11,
        "replayed": False,
    }
    gets = [r for r in requests if r[0] == "GET"]
    posts = [r for r in requests if r[0] == "POST"]
    assert len(gets) == 3  # both drives and the parent
    assert len(posts) == 1
    conformer = posts[0][2]["conformers"][0]
    opt_key = conformer["primary_calculation"]["key"]
    assert conformer["primary_calculation"]["type"] == "opt"
    assert [c["key"] for c in conformer["additional_calculations"]] == ["qcschema_scan", "qcschema_scan_2"]
    for scan_calc, atoms in zip(conformer["additional_calculations"], ([1, 2, 3, 4], [2, 3, 4, 5]), strict=True):
        assert scan_calc["type"] == "scan"
        assert scan_calc["depends_on"] == [{"parent_calculation_key": opt_key, "role": "scan_parent"}]
        (coordinate,) = scan_calc["scan_result"]["coordinates"]
        assert [coordinate[f"atom{i}_index"] for i in range(1, 5)] == atoms
        assert len(scan_calc["scan_result"]["points"]) == 4
    assert [a["sha256"] for c in conformer["additional_calculations"] for a in c["artifacts"]] == [
        sha256_bytes(ROTOR1.read_bytes()),
        sha256_bytes(ROTOR2.read_bytes()),
    ]


def test_a_second_rotor_alone_is_refused_without_suggesting_a_duplicate(api, capsys, monkeypatch) -> None:
    """The first rotor and its parent are already deposited. Importing the
    second rotor alone would post the parent again; the refusal says to
    import the drives together and does not suggest --allow-duplicate."""
    base_url, requests, deposited = api
    deposited.update({sha256_bytes(ROTOR1.read_bytes()), sha256_bytes(PARENT_3.read_bytes())})
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test_key_value_1234")
    rc = main(
        ["import", str(ROTOR2), "--parent-opt", str(PARENT_3), "--smiles", "OOO", "--upload", "--base-url", base_url]
    )
    line = _one_line(capsys.readouterr().err)
    assert rc == 2
    assert line.startswith("REFUSED [scan_parent_already_imported]:")
    assert "DRIVE1.json DRIVE2.json" in line
    assert "--allow-duplicate" not in line
    assert not [r for r in requests if r[0] == "POST"]
