"""Tests for the ``tckdb-replay`` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tckdb_client import cli


def _write_pending_conformer(tmp_path: Path) -> Path:
    sub = tmp_path / "conformer_calculation"
    sub.mkdir(parents=True, exist_ok=True)
    payload_path = sub / "spc.cnf.payload.json"
    payload_path.write_text(json.dumps({"species": {"label": "X"}}))
    sidecar_path = sub / "spc.cnf.meta.json"
    sidecar_path.write_text(
        json.dumps(
            {
                "bundle_format_version": "0",
                "payload_kind": "conformer_calculation",
                "endpoint": "/uploads/conformers",
                "idempotency_key": "tckdb_conformer_v0_0123456789abcdef",
                "base_url": "http://from-sidecar.example/api/v1",
                "status": "pending",
                "payload_file": payload_path.name,
            }
        )
    )
    return sidecar_path


# ---------------------------------------------------------------------------
# Bundle dir handling
# ---------------------------------------------------------------------------


def test_missing_bundle_dir_exits_3(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    missing = tmp_path / "does_not_exist"
    rc = cli.main([str(missing)])
    assert rc == cli.EXIT_BUNDLE_DIR
    err = capsys.readouterr().err
    assert "does not exist" in err or "not a directory" in err


def test_empty_bundle_dir_exits_0(tmp_path: Path) -> None:
    empty = tmp_path / "empty_bundle"
    empty.mkdir()
    rc = cli.main([str(empty), "--dry-run"])
    assert rc == cli.EXIT_OK


def test_help_text_lists_computed_species(
    capsys: pytest.CaptureFixture,
) -> None:
    """``tckdb-replay --help`` must advertise the supported payload
    kinds, including ``computed_species``."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "computed_species" in out
    assert "conformer_calculation" in out
    assert "calculation_artifact" in out


# ---------------------------------------------------------------------------
# API key handling
# ---------------------------------------------------------------------------


def test_dry_run_does_not_require_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pending_conformer(tmp_path)
    monkeypatch.delenv("TCKDB_API_KEY", raising=False)
    monkeypatch.delenv("MY_OTHER_KEY", raising=False)
    rc = cli.main([str(tmp_path), "--dry-run"])
    assert rc == cli.EXIT_OK


def test_non_dry_run_requires_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pending_conformer(tmp_path)
    monkeypatch.delenv("TCKDB_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        cli.main([str(tmp_path)])
    # argparse.error() exits with EXIT_ARGPARSE.
    assert exc_info.value.code == cli.EXIT_ARGPARSE


# ---------------------------------------------------------------------------
# End-to-end replay via CLI with a stubbed factory
# ---------------------------------------------------------------------------


def test_cli_replay_success_via_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    sp = _write_pending_conformer(tmp_path)

    captured = {"factory_calls": [], "calls": []}

    class _StubClient:
        def __init__(self, base_url: str, api_key, timeout):
            self.base_url = base_url
            captured["factory_calls"].append(base_url)

        def request_json(self, method, path, *, json=None, idempotency_key=None,
                         authenticated=True, extra_headers=None):
            from tckdb_client.client import TCKDBResponse

            captured["calls"].append((method, path, idempotency_key))
            return TCKDBResponse(
                data={"id": 1},
                status_code=201,
                headers={"Idempotency-Replayed": "false"},
            )

        def close(self):
            pass

    monkeypatch.setattr(cli, "TCKDBClient", _StubClient)
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test_key_value_1234")

    rc = cli.main([str(tmp_path), "--base-url", "http://override.example/api/v1"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "uploaded" in out
    # CLI override won precedence:
    assert captured["factory_calls"] == ["http://override.example/api/v1"]
    assert captured["calls"][0][0] == "POST"
    assert captured["calls"][0][2] == "tckdb_conformer_v0_0123456789abcdef"

    updated = json.loads(sp.read_text())
    assert updated["status"] == "uploaded"


def test_cli_returns_1_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_pending_conformer(tmp_path)
    from tckdb_client.errors import TCKDBHTTPError

    class _FailingClient:
        def __init__(self, base_url: str, api_key, timeout):
            pass

        def request_json(self, *a, **kw):
            raise TCKDBHTTPError(
                "boom",
                status_code=500,
                code="server_error",
                detail="boom",
                response_json={"detail": "boom"},
                response_text=None,
                headers={},
            )

        def close(self):
            pass

    monkeypatch.setattr(cli, "TCKDBClient", _FailingClient)
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test_key_value_1234")
    rc = cli.main([str(tmp_path)])
    assert rc == cli.EXIT_FAILURES


def test_cli_groups_failures_by_error(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Many sidecars sharing the same error collapse into one printed row."""
    # Three sidecars, all with the same (missing) bundle_format_version
    # → identical last_error → one grouped row.
    for name in ("a", "b", "c"):
        sub = tmp_path / "conformer_calculation"
        sub.mkdir(parents=True, exist_ok=True)
        payload = sub / f"{name}.payload.json"
        payload.write_text("{}")
        (sub / f"{name}.meta.json").write_text(
            json.dumps(
                {
                    # bundle_format_version intentionally omitted.
                    "payload_kind": "conformer_calculation",
                    "endpoint": "/uploads/conformers",
                    "idempotency_key": "tckdb_demo_v0_0123456789abcdef",
                    "base_url": "http://example.test/api/v1",
                    "status": "pending",
                    "payload_file": payload.name,
                }
            )
        )

    rc = cli.main([str(tmp_path), "--dry-run"])
    # Dry-run still walks, so the version-mismatch failure path fires
    # before the dispatch step (the engine validates version eagerly).
    out = capsys.readouterr().out
    assert "Failure breakdown" in out
    # Single grouped row with count 3, not three separate lines.
    assert "3× [conformer_calculation]" in out
    assert "bundle_format_version" in out
    # Exit 1 because there are failures.
    assert rc == cli.EXIT_FAILURES


def test_cli_caps_failure_groups_and_mentions_show_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_FAILURE_GROUPS_SHOWN", 2)
    # Three distinct error groups (different bogus versions).
    for name, ver in [("a", "1"), ("b", "2"), ("c", "3")]:
        sub = tmp_path / "conformer_calculation"
        sub.mkdir(parents=True, exist_ok=True)
        payload = sub / f"{name}.payload.json"
        payload.write_text("{}")
        (sub / f"{name}.meta.json").write_text(
            json.dumps(
                {
                    "bundle_format_version": ver,
                    "payload_kind": "conformer_calculation",
                    "endpoint": "/uploads/conformers",
                    "idempotency_key": "tckdb_demo_v0_0123456789abcdef",
                    "base_url": "http://example.test/api/v1",
                    "status": "pending",
                    "payload_file": payload.name,
                }
            )
        )

    cli.main([str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out
    assert "and 1 more distinct error" in out
    assert "--show-failures" in out


def test_cli_idempotency_key_header_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: the idempotency_key from the sidecar is the one POSTed.

    The HTTP layer is responsible for header injection; here we just
    confirm the engine plumbed the value through the client call.
    """
    _write_pending_conformer(tmp_path)
    seen = {}

    class _Stub:
        def __init__(self, *_a, **_kw): pass

        def request_json(self, method, path, *, json=None, idempotency_key=None, **_):
            from tckdb_client.client import TCKDBResponse
            seen["idem"] = idempotency_key
            return TCKDBResponse(data={}, status_code=201, headers={})

        def close(self): pass

    monkeypatch.setattr(cli, "TCKDBClient", _Stub)
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test_key_value_1234")
    rc = cli.main([str(tmp_path)])
    assert rc == cli.EXIT_OK
    assert seen["idem"] == "tckdb_conformer_v0_0123456789abcdef"
