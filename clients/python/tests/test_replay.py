"""Tests for the offline replay engine.

The engine is exercised through fake client factories. We never touch
httpx here — the engine's contract with the client is duck-typed
(``request_json``, ``close``), and the tests assert behaviour against
that contract directly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

from tckdb_client.client import TCKDBResponse
from tckdb_client.errors import TCKDBHTTPError, TCKDBValidationError
from tckdb_client.replay import (
    RESPONSE_BODY_CAP,
    SUPPORTED_PAYLOAD_KINDS,
    ReplaySummary,
    replay_bundle,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _Call:
    base_url: str
    method: str
    path: str
    json: Any
    idempotency_key: str | None


class _FakeClient:
    def __init__(
        self,
        base_url: str,
        *,
        recorder: "_FactoryRecorder",
        responder: Callable[["_FakeClient", str, str, Any, str | None], Any],
    ) -> None:
        self.base_url = base_url
        self._recorder = recorder
        self._responder = responder
        self.closed = False

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        idempotency_key: str | None = None,
        authenticated: bool = True,
        extra_headers: Any = None,
    ) -> TCKDBResponse:
        call = _Call(self.base_url, method, path, json, idempotency_key)
        self._recorder.calls.append(call)
        return self._responder(self, method, path, json, idempotency_key)

    def close(self) -> None:
        self.closed = True


@dataclass
class _FactoryRecorder:
    factory_calls: list[str] = field(default_factory=list)
    calls: list[_Call] = field(default_factory=list)
    clients: list[_FakeClient] = field(default_factory=list)


def _make_factory(
    recorder: _FactoryRecorder,
    *,
    responder: Callable[..., Any] | None = None,
):
    if responder is None:
        responder = _ok_201

    def factory(base_url: str):
        recorder.factory_calls.append(base_url)
        client = _FakeClient(base_url, recorder=recorder, responder=responder)
        recorder.clients.append(client)
        return client

    return factory


def _ok_201(client, method, path, body, idempotency_key):
    return TCKDBResponse(
        data={"id": 42, "status": "stored"},
        status_code=201,
        headers={"Idempotency-Replayed": "false"},
    )


def _ok_replayed(client, method, path, body, idempotency_key):
    return TCKDBResponse(
        data={"id": 42, "status": "stored"},
        status_code=200,
        headers={"Idempotency-Replayed": "true"},
    )


def _raise_500(client, method, path, body, idempotency_key):
    raise TCKDBHTTPError(
        "internal server error",
        status_code=500,
        code="server_error",
        detail="boom",
        response_json={"detail": "boom"},
        response_text=None,
        headers={},
    )


# ---------------------------------------------------------------------------
# Bundle fixture builders
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def _write_conformer_sidecar(
    bundle_dir: Path,
    *,
    name: str = "spc1.cnf1",
    status: str = "pending",
    base_url: str | None = "http://producer.example/api/v1",
    bundle_format_version: str | None = "0",
    payload_kind: str = "conformer_calculation",
    payload: dict | None = None,
    payload_file_override: str | None = None,
    extra_sidecar: dict | None = None,
) -> tuple[Path, Path]:
    sub = bundle_dir / "conformer_calculation"
    sub.mkdir(parents=True, exist_ok=True)
    payload_path = sub / f"{name}.payload.json"
    if payload_file_override is None:
        _write_json(payload_path, payload or {"species": {"label": "X"}})
        payload_file_value = payload_path.name
    else:
        if payload is not None:
            _write_json(payload_path, payload)
        payload_file_value = payload_file_override

    sidecar = {
        "payload_kind": payload_kind,
        "endpoint": "/uploads/conformers",
        "idempotency_key": "tckdb_conformer_v0_0123456789abcdef",
        "status": status,
        "payload_file": payload_file_value,
    }
    if bundle_format_version is not None:
        sidecar["bundle_format_version"] = bundle_format_version
    if base_url is not None:
        sidecar["base_url"] = base_url
    if extra_sidecar:
        sidecar.update(extra_sidecar)

    sidecar_path = sub / f"{name}.meta.json"
    _write_json(sidecar_path, sidecar)
    return sidecar_path, payload_path


def _write_artifact_sidecar(
    bundle_dir: Path,
    *,
    name: str = "spc1.calc7.output_log",
    status: str = "pending",
    base_url: str | None = "http://producer.example/api/v1",
    bundle_format_version: str | None = "0",
    content: bytes = b"hello world",
    declared_sha: str | None = None,
    declared_bytes: int | None = None,
    write_source: bool = True,
    source_path_override: str | None = None,
    kind: str | None = "output_log",
    filename: str | None = "output.log",
    extra_sidecar: dict | None = None,
) -> tuple[Path, Path]:
    sub = bundle_dir / "calculation_artifacts"
    sub.mkdir(parents=True, exist_ok=True)
    source_path = sub / f"{name}.source"
    if write_source:
        source_path.write_bytes(content)
    src_value = source_path_override if source_path_override is not None else source_path.name

    sha = (
        declared_sha
        if declared_sha is not None
        else hashlib.sha256(content).hexdigest()
    )
    nbytes = declared_bytes if declared_bytes is not None else len(content)

    sidecar = {
        "payload_kind": "calculation_artifact",
        "endpoint": "/calculations/7/artifacts",
        "idempotency_key": "tckdb_artifact_v0_0123456789abcdef",
        "status": status,
        "source_path": src_value,
        "sha256": sha,
        "bytes": nbytes,
        "calculation_id": 7,
    }
    if bundle_format_version is not None:
        sidecar["bundle_format_version"] = bundle_format_version
    if base_url is not None:
        sidecar["base_url"] = base_url
    if kind is not None:
        sidecar["kind"] = kind
    if filename is not None:
        sidecar["filename"] = filename
    if extra_sidecar:
        sidecar.update(extra_sidecar)

    sidecar_path = sub / f"{name}.meta.json"
    _write_json(sidecar_path, sidecar)
    return sidecar_path, source_path


def _read_sidecar(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Tests — discovery & status filtering
# ---------------------------------------------------------------------------


def test_discovers_sidecars_recursively(tmp_path: Path) -> None:
    _write_conformer_sidecar(tmp_path, name="a")
    _write_artifact_sidecar(tmp_path, name="b")
    deep = tmp_path / "extras"
    _write_conformer_sidecar(deep, name="c")

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.total == 3
    assert summary.uploaded == 3


def test_skips_uploaded_sidecars_by_default(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path, status="uploaded")
    pre = _read_sidecar(sp)

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.uploaded == 0
    assert summary.skipped_already_uploaded == 1
    assert recorder.calls == []
    assert _read_sidecar(sp) == pre


def test_skips_marked_skipped_by_default(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path, status="skipped")
    pre = _read_sidecar(sp)

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.skipped_marked_skipped == 1
    assert summary.uploaded == 0
    assert recorder.calls == []
    assert _read_sidecar(sp) == pre


def test_only_pending_skips_failed(tmp_path: Path) -> None:
    pending_sp, _ = _write_conformer_sidecar(tmp_path, name="p", status="pending")
    failed_sp, _ = _write_conformer_sidecar(
        tmp_path,
        name="f",
        status="failed",
        extra_sidecar={"last_error": "HTTP 503 transient"},
    )
    failed_pre = _read_sidecar(failed_sp)

    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder),
        only_pending=True,
    )
    assert summary.uploaded == 1
    # The deferred failed sidecar gets its own counter — never lumped
    # with already-uploaded sidecars, which would hide unfinished work.
    assert summary.skipped_failed_due_to_only_pending == 1
    assert summary.skipped_already_uploaded == 0
    assert _read_sidecar(failed_sp) == failed_pre
    # Pending sidecar is now uploaded.
    assert _read_sidecar(pending_sp)["status"] == "uploaded"


# ---------------------------------------------------------------------------
# Tests — happy paths
# ---------------------------------------------------------------------------


def test_replays_pending_conformer_posts_payload_unchanged(tmp_path: Path) -> None:
    payload = {"species": {"label": "CH4"}, "conformer": {"e0": 1.23}}
    sp, _ = _write_conformer_sidecar(tmp_path, payload=payload)

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert summary.uploaded == 1
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call.method == "POST"
    assert call.path == "/uploads/conformers"
    assert call.json == payload  # POSTed unchanged
    assert call.idempotency_key == "tckdb_conformer_v0_0123456789abcdef"

    updated = _read_sidecar(sp)
    assert updated["status"] == "uploaded"
    assert updated["response_status_code"] == 201
    assert updated["idempotency_replayed"] is False
    assert updated["last_error"] is None
    assert "uploaded_at" in updated


def test_replays_failed_conformer_clears_last_error(tmp_path: Path) -> None:
    payload = {"species": {"label": "OH"}}
    sp, _ = _write_conformer_sidecar(
        tmp_path,
        status="failed",
        payload=payload,
        extra_sidecar={"last_error": "HTTP 503 transient"},
    )

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert summary.uploaded == 1
    updated = _read_sidecar(sp)
    assert updated["status"] == "uploaded"
    assert updated["last_error"] is None


def test_idempotency_replayed_flag_persisted(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path)
    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=_ok_replayed),
    )
    assert summary.uploaded == 1
    assert _read_sidecar(sp)["idempotency_replayed"] is True


def test_replays_calculation_artifact_with_recomputed_hashes(tmp_path: Path) -> None:
    content = b"orca output bytes"
    sp, src_path = _write_artifact_sidecar(tmp_path, content=content)

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert summary.uploaded == 1
    call = recorder.calls[0]
    assert call.method == "POST"
    assert call.path == "/calculations/7/artifacts"
    assert call.idempotency_key == "tckdb_artifact_v0_0123456789abcdef"
    body = call.json
    assert isinstance(body, dict)
    artifacts = body["artifacts"]
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art["kind"] == "output_log"
    assert art["filename"] == "output.log"
    expected_b64 = base64.b64encode(content).decode("ascii")
    assert art["content_base64"] == expected_b64
    assert art["sha256"] == hashlib.sha256(content).hexdigest()
    assert art["bytes"] == len(content)


def test_artifact_filename_falls_back_to_basename(tmp_path: Path) -> None:
    sp, src_path = _write_artifact_sidecar(
        tmp_path, content=b"x", filename=None
    )
    recorder = _FactoryRecorder()
    replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert recorder.calls[0].json["artifacts"][0]["filename"] == src_path.name


# ---------------------------------------------------------------------------
# Tests — base URL precedence
# ---------------------------------------------------------------------------


def test_base_url_override_wins(tmp_path: Path) -> None:
    _write_conformer_sidecar(
        tmp_path,
        base_url="http://from-sidecar.example/api/v1",
    )
    recorder = _FactoryRecorder()
    replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder),
        base_url_override="http://override.example/api/v1",
    )
    assert recorder.factory_calls == ["http://override.example/api/v1"]


def test_sidecar_base_url_used_when_no_override(tmp_path: Path) -> None:
    _write_conformer_sidecar(
        tmp_path,
        base_url="http://from-sidecar.example/api/v1",
    )
    recorder = _FactoryRecorder()
    replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert recorder.factory_calls == ["http://from-sidecar.example/api/v1"]


def test_missing_base_url_marks_failed(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path, base_url=None)
    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.failed == 1
    assert recorder.calls == []
    updated = _read_sidecar(sp)
    assert updated["status"] == "failed"
    assert "base_url" in updated["last_error"]


def test_factory_called_per_sidecar_with_heterogeneous_urls(tmp_path: Path) -> None:
    _write_conformer_sidecar(tmp_path, name="a", base_url="http://one.example/v1")
    _write_conformer_sidecar(tmp_path, name="b", base_url="http://one.example/v1")
    _write_conformer_sidecar(tmp_path, name="c", base_url="http://two.example/v1")

    recorder = _FactoryRecorder()
    replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert sorted(recorder.factory_calls) == [
        "http://one.example/v1",
        "http://one.example/v1",
        "http://two.example/v1",
    ]
    assert all(c.closed for c in recorder.clients)


# ---------------------------------------------------------------------------
# Tests — version & dispatch errors
# ---------------------------------------------------------------------------


def test_unsupported_format_version_marks_failed(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path, bundle_format_version="1")
    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.failed == 1
    assert recorder.calls == []
    updated = _read_sidecar(sp)
    assert updated["status"] == "failed"
    assert "bundle_format_version" in updated["last_error"]


def test_missing_format_version_marks_failed(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path, bundle_format_version=None)
    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.failed == 1
    updated = _read_sidecar(sp)
    assert updated["status"] == "failed"
    assert "bundle_format_version" in updated["last_error"]


def test_unknown_payload_kind_marks_failed(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path, payload_kind="future_kind_xyz")
    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.failed == 1
    assert recorder.calls == []
    updated = _read_sidecar(sp)
    assert updated["status"] == "failed"
    assert "future_kind_xyz" in updated["last_error"]


# ---------------------------------------------------------------------------
# Tests — local errors before HTTP
# ---------------------------------------------------------------------------


def test_missing_conformer_payload_file_marks_failed(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(
        tmp_path, payload_file_override="does_not_exist.payload.json"
    )
    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.failed == 1
    assert recorder.calls == []
    updated = _read_sidecar(sp)
    assert "does_not_exist" in updated["last_error"]


def test_missing_artifact_source_marks_failed(tmp_path: Path) -> None:
    sp, _ = _write_artifact_sidecar(
        tmp_path, source_path_override="missing.source", write_source=False
    )
    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.failed == 1
    assert recorder.calls == []
    updated = _read_sidecar(sp)
    assert "missing.source" in updated["last_error"]


def test_artifact_sha_drift_marks_failed_without_post(tmp_path: Path) -> None:
    sp, _ = _write_artifact_sidecar(
        tmp_path, content=b"abc", declared_sha="a" * 64
    )
    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.failed == 1
    assert recorder.calls == []
    updated = _read_sidecar(sp)
    assert "file drift" in updated["last_error"]


def test_artifact_bytes_drift_marks_failed_without_post(tmp_path: Path) -> None:
    sp, _ = _write_artifact_sidecar(
        tmp_path, content=b"abc", declared_bytes=999
    )
    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.failed == 1
    assert recorder.calls == []
    updated = _read_sidecar(sp)
    assert "file drift" in updated["last_error"]


# ---------------------------------------------------------------------------
# Tests — atomic write semantics
# ---------------------------------------------------------------------------


def test_successful_replay_writes_atomically(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path)
    recorder = _FactoryRecorder()
    replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    # Sidecar is parseable, status uploaded, and no .tmp leftovers in the dir.
    updated = _read_sidecar(sp)
    assert updated["status"] == "uploaded"
    leftovers = list(sp.parent.glob("*.tmp"))
    assert leftovers == []


def test_failed_http_replay_persists_response_body(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path)
    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=_raise_500),
    )
    assert summary.failed == 1
    updated = _read_sidecar(sp)
    assert updated["status"] == "failed"
    assert updated["response_status_code"] == 500
    # response_body holds the parsed JSON (small enough to fit untruncated).
    assert updated["response_body"] == {"detail": "boom"}
    assert updated["response_body_truncated"] is False
    assert "HTTP 500" in updated["last_error"]


def test_atomic_write_failure_does_not_corrupt_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path)
    pre = _read_sidecar(sp)

    real_replace = os.replace
    calls = {"n": 0}

    def boom_replace(src, dst):
        if str(dst) == str(sp):
            calls["n"] += 1
            raise OSError("simulated mid-rename failure")
        return real_replace(src, dst)

    monkeypatch.setattr("tckdb_client.replay.os.replace", boom_replace)

    recorder = _FactoryRecorder()
    # The HTTP upload still succeeds, but persistence fails. The engine
    # logs and counts uploaded — but the on-disk sidecar must stay intact.
    replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert calls["n"] >= 1
    # Original sidecar JSON is unchanged on disk.
    assert _read_sidecar(sp) == pre


# ---------------------------------------------------------------------------
# Tests — dry-run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_post_or_mutate(tmp_path: Path) -> None:
    sp_a, _ = _write_conformer_sidecar(tmp_path, name="a")
    sp_b, _ = _write_artifact_sidecar(tmp_path, name="b")
    pre_a = _read_sidecar(sp_a)
    pre_b = _read_sidecar(sp_b)

    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder),
        dry_run=True,
    )

    assert recorder.factory_calls == []
    assert recorder.calls == []
    assert summary.dry_run == 2
    assert summary.uploaded == 0
    # Files unchanged.
    assert _read_sidecar(sp_a) == pre_a
    assert _read_sidecar(sp_b) == pre_b


# ---------------------------------------------------------------------------
# Tests — bounded response body policy
# ---------------------------------------------------------------------------


def test_summary_records_failure_details(tmp_path: Path) -> None:
    """Every failure carries (sidecar_path, payload_kind, last_error)."""
    sp1, _ = _write_conformer_sidecar(
        tmp_path, name="a", bundle_format_version="1"
    )
    sp2, _ = _write_conformer_sidecar(
        tmp_path,
        name="b",
        payload_file_override="missing.payload.json",
    )
    # Hand-corrupt sidecar c to force a parse failure.
    bad_dir = tmp_path / "conformer_calculation"
    bad_dir.mkdir(exist_ok=True)
    (bad_dir / "c.meta.json").write_text("{not json")

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert summary.failed == 3
    assert len(summary.failures) == 3
    by_path = {f.sidecar_path: f for f in summary.failures}
    assert "bundle_format_version" in by_path[str(sp1)].last_error
    assert "missing.payload.json" in by_path[str(sp2)].last_error
    assert by_path[str(sp1)].payload_kind == "conformer_calculation"
    parse_failure = next(
        f for f in summary.failures
        if f.payload_kind == "__unparseable__"
    )
    assert "parse failure" in parse_failure.last_error


def test_large_response_body_is_truncated(tmp_path: Path) -> None:
    sp, _ = _write_conformer_sidecar(tmp_path)

    big = "x" * (RESPONSE_BODY_CAP * 2)

    def big_responder(client, method, path, body, idem):
        return TCKDBResponse(
            data={"big": big},
            status_code=201,
            headers={"Idempotency-Replayed": "false"},
        )

    recorder = _FactoryRecorder()
    replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=big_responder),
    )
    updated = _read_sidecar(sp)
    assert updated["response_body_truncated"] is True
    assert isinstance(updated["response_body"], str)
    assert len(updated["response_body"]) == RESPONSE_BODY_CAP
    assert updated["response_body_original_length"] > RESPONSE_BODY_CAP


# ---------------------------------------------------------------------------
# Tests — computed_species (DR-0029)
# ---------------------------------------------------------------------------


def _write_computed_species_sidecar(
    bundle_dir: Path,
    *,
    name: str = "water.computed_species",
    status: str = "pending",
    base_url: str | None = "http://producer.example/api/v1",
    bundle_format_version: str | None = "0",
    endpoint: str = "/api/v1/uploads/computed-species",
    idempotency_key: str = "tckdb_cs_v0_0123456789abcdef",
    payload: dict | None = None,
    payload_file_override: str | None = None,
    extra_sidecar: dict | None = None,
) -> tuple[Path, Path]:
    """Lay down a minimal computed_species bundle: payload JSON + sidecar.

    The bundle on disk mirrors what the future producer side will emit
    when ARC starts saving offline computed-species bundles. The
    payload's contents are intentionally opaque — the replay engine
    must POST whatever JSON it finds, unchanged.
    """
    sub = bundle_dir / "computed_species"
    sub.mkdir(parents=True, exist_ok=True)
    payload_path = sub / f"{name}.payload.json"
    if payload_file_override is None:
        _write_json(payload_path, payload or {"species_entry": {"smiles": "O"}})
        payload_file_value = payload_path.name
    else:
        if payload is not None:
            _write_json(payload_path, payload)
        payload_file_value = payload_file_override

    sidecar = {
        "payload_kind": "computed_species",
        "endpoint": endpoint,
        "idempotency_key": idempotency_key,
        "status": status,
        "payload_file": payload_file_value,
    }
    if bundle_format_version is not None:
        sidecar["bundle_format_version"] = bundle_format_version
    if base_url is not None:
        sidecar["base_url"] = base_url
    if extra_sidecar:
        sidecar.update(extra_sidecar)

    sidecar_path = sub / f"{name}.meta.json"
    _write_json(sidecar_path, sidecar)
    return sidecar_path, payload_path


def test_computed_species_sidecar_is_discovered_and_replayed(tmp_path: Path) -> None:
    """End-to-end: a computed_species sidecar is walked, dispatched,
    and the saved JSON is POSTed unchanged. The sidecar status flips
    to ``uploaded``."""
    payload = {
        "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
        "conformers": [{"key": "c0", "geometry": {"xyz_text": "..."}}],
        "thermo": {"h298_kj_mol": -241.8},
    }
    sp, _ = _write_computed_species_sidecar(tmp_path, payload=payload)

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert summary.uploaded == 1
    assert summary.by_kind["computed_species"]["uploaded"] == 1
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call.method == "POST"
    # Endpoint comes from the sidecar, not from any client default.
    assert call.path == "/api/v1/uploads/computed-species"
    # Saved JSON POSTed unchanged — chemistry-blind.
    assert call.json == payload
    # Idempotency-Key header sent (passed through ``request_json``'s
    # idempotency_key parameter, which the client maps to the header).
    assert call.idempotency_key == "tckdb_cs_v0_0123456789abcdef"

    updated = _read_sidecar(sp)
    assert updated["status"] == "uploaded"
    assert updated["response_status_code"] == 201
    assert updated["last_error"] is None


def test_computed_species_endpoint_taken_from_sidecar(tmp_path: Path) -> None:
    """Custom endpoint path on a computed_species sidecar wins."""
    sp, _ = _write_computed_species_sidecar(
        tmp_path,
        endpoint="/api/v2/uploads/computed-species",
    )
    recorder = _FactoryRecorder()
    replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert recorder.calls[0].path == "/api/v2/uploads/computed-species"


def test_computed_species_http_failure_marks_failed(tmp_path: Path) -> None:
    """A 500 from the server flips the sidecar to ``failed`` with the
    error captured."""
    sp, _ = _write_computed_species_sidecar(tmp_path)
    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=_raise_500),
    )
    assert summary.failed == 1
    assert summary.uploaded == 0
    updated = _read_sidecar(sp)
    assert updated["status"] == "failed"
    assert updated["response_status_code"] == 500
    assert "HTTP 500" in updated["last_error"]


def test_computed_species_dry_run_no_post_no_mutation(tmp_path: Path) -> None:
    """Dry-run discovers the sidecar but neither POSTs nor mutates it."""
    sp, _ = _write_computed_species_sidecar(tmp_path)
    pre = _read_sidecar(sp)

    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder),
        dry_run=True,
    )

    assert summary.dry_run == 1
    assert summary.uploaded == 0
    assert summary.by_kind["computed_species"]["dry_run"] == 1
    # No HTTP, no factory call.
    assert recorder.factory_calls == []
    assert recorder.calls == []
    # Sidecar untouched on disk.
    assert _read_sidecar(sp) == pre


def test_unsupported_payload_kind_marks_failed_unchanged(tmp_path: Path) -> None:
    """Unknown ``payload_kind`` behaviour is unchanged: the engine still
    marks the sidecar failed with ``unknown payload_kind`` regardless
    of how many supported kinds the dispatch table grows."""
    sp, _ = _write_computed_species_sidecar(
        tmp_path,
        extra_sidecar={"payload_kind": "thermo_block"},
    )
    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))
    assert summary.failed == 1
    assert recorder.calls == []
    updated = _read_sidecar(sp)
    assert updated["status"] == "failed"
    assert "unknown payload_kind" in updated["last_error"]
    assert "thermo_block" in updated["last_error"]


def test_supported_payload_kinds_includes_computed_species() -> None:
    """The CLI/help-text source of truth advertises the new kind."""
    assert "computed_species" in SUPPORTED_PAYLOAD_KINDS
    # The two pre-existing kinds remain present.
    assert "conformer_calculation" in SUPPORTED_PAYLOAD_KINDS
    assert "calculation_artifact" in SUPPORTED_PAYLOAD_KINDS


# ---------------------------------------------------------------------------
# Tests — computed_reaction (mirror current backend contract)
# ---------------------------------------------------------------------------


def _write_computed_reaction_sidecar(
    bundle_dir: Path,
    *,
    name: str = "rxn0.computed_reaction",
    status: str = "pending",
    base_url: str | None = "http://producer.example/api/v1",
    bundle_format_version: str | None = "0",
    endpoint: str = "/api/v1/uploads/computed-reaction",
    idempotency_key: str = "tckdb_cr_v0_0123456789abcdef",
    payload: dict | None = None,
    payload_file_override: str | None = None,
    extra_sidecar: dict | None = None,
) -> tuple[Path, Path]:
    """Lay down a minimal computed_reaction bundle: payload JSON + sidecar.

    The replay engine must POST whatever JSON it finds, unchanged. The
    payload contents are intentionally opaque to the engine — adapters
    decide what to put in; the client just transports it.
    """
    sub = bundle_dir / "computed_reaction"
    sub.mkdir(parents=True, exist_ok=True)
    payload_path = sub / f"{name}.payload.json"
    if payload_file_override is None:
        _write_json(
            payload_path,
            payload or {"species": [], "reactant_keys": [], "product_keys": []},
        )
        payload_file_value = payload_path.name
    else:
        if payload is not None:
            _write_json(payload_path, payload)
        payload_file_value = payload_file_override

    sidecar = {
        "payload_kind": "computed_reaction",
        "endpoint": endpoint,
        "idempotency_key": idempotency_key,
        "status": status,
        "payload_file": payload_file_value,
    }
    if bundle_format_version is not None:
        sidecar["bundle_format_version"] = bundle_format_version
    if base_url is not None:
        sidecar["base_url"] = base_url
    if extra_sidecar:
        sidecar.update(extra_sidecar)

    sidecar_path = sub / f"{name}.meta.json"
    _write_json(sidecar_path, sidecar)
    return sidecar_path, payload_path


def test_supported_payload_kinds_includes_computed_reaction() -> None:
    """Replay engine advertises computed_reaction so CLI help/docs see it."""
    assert "computed_reaction" in SUPPORTED_PAYLOAD_KINDS


def test_computed_reaction_legacy_payload_replays_unchanged(tmp_path: Path) -> None:
    """Backward-compat: a computed_reaction payload that omits the new
    fields (input_geometries, output_geometries, depends_on,
    source_calculations) still discovers, dispatches, and POSTs unchanged.
    """
    payload = {
        "species": [
            {
                "key": "r0",
                "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
                "conformers": [],
                "calculations": [],
            }
        ],
        "reactant_keys": ["r0"],
        "product_keys": ["r0"],
        "kinetics": [
            {
                "reactant_keys": ["r0"],
                "product_keys": ["r0"],
                "model_kind": "modified_arrhenius",
                "a": 1.2e6,
                "a_units": "cm3_mol_s",
                "n": 1.5,
                "reported_ea": 42.0,
                "reported_ea_units": "kj_mol",
            }
        ],
    }
    sp, _ = _write_computed_reaction_sidecar(tmp_path, payload=payload)

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert summary.uploaded == 1
    assert summary.by_kind["computed_reaction"]["uploaded"] == 1
    call = recorder.calls[0]
    assert call.method == "POST"
    assert call.path == "/api/v1/uploads/computed-reaction"
    assert call.json == payload
    assert call.idempotency_key == "tckdb_cr_v0_0123456789abcdef"

    updated = _read_sidecar(sp)
    assert updated["status"] == "uploaded"
    assert updated["response_status_code"] == 201
    assert updated["last_error"] is None


def test_computed_reaction_calc_provenance_fields_replay_unchanged(
    tmp_path: Path,
) -> None:
    """A computed_reaction payload carrying input_geometries,
    output_geometries, and depends_on on a calculation must round-trip
    through replay byte-for-byte. The client is dict-passthrough; this
    test pins that contract.
    """
    payload = {
        "species": [],
        "reactant_keys": ["r0"],
        "product_keys": ["p0"],
        "transition_state": {
            "charge": 0,
            "multiplicity": 2,
            "geometry": {"key": "ts-geom", "xyz_text": "1\n\nH 0 0 0"},
            "calculation": {
                "key": "ts_opt",
                "type": "opt",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
            },
            "calculations": [
                {
                    "key": "ts_freq",
                    "type": "freq",
                    "geometry_key": "ts-geom",
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
                    "input_geometries": [
                        {"xyz_text": "1\n\nH 0 0 0"},
                    ],
                    "output_geometries": [
                        {
                            "geometry": {"xyz_text": "1\n\nH 0 0 0"},
                            "role": "final",
                        }
                    ],
                    "depends_on": [
                        {
                            "parent_calculation_key": "ts_opt",
                            "role": "freq_on",
                        }
                    ],
                }
            ],
        },
    }
    sp, _ = _write_computed_reaction_sidecar(tmp_path, payload=payload)

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert summary.uploaded == 1
    posted = recorder.calls[0].json
    # The dict reaches the wire identical to what was on disk.
    assert posted == payload
    # Spot-check the new fields specifically — sanity guard against
    # accidental key normalization in the engine.
    ts_freq = posted["transition_state"]["calculations"][0]
    assert ts_freq["input_geometries"] == [{"xyz_text": "1\n\nH 0 0 0"}]
    assert ts_freq["output_geometries"] == [
        {"geometry": {"xyz_text": "1\n\nH 0 0 0"}, "role": "final"}
    ]
    assert ts_freq["depends_on"] == [
        {"parent_calculation_key": "ts_opt", "role": "freq_on"}
    ]


def test_computed_reaction_kinetics_source_calculations_replay_unchanged(
    tmp_path: Path,
) -> None:
    """A computed_reaction kinetics block carrying source_calculations
    must round-trip through replay byte-for-byte (calculation_key + role
    pairs preserved in declared order).
    """
    payload = {
        "species": [],
        "reactant_keys": ["r0"],
        "product_keys": ["p0"],
        "kinetics": [
            {
                "reactant_keys": ["r0"],
                "product_keys": ["p0"],
                "model_kind": "modified_arrhenius",
                "a": 1200000.0,
                "a_units": "cm3_mol_s",
                "n": 1.5,
                "reported_ea": 42.0,
                "reported_ea_units": "kj_mol",
                "source_calculations": [
                    {"calculation_key": "reactant0_sp", "role": "reactant_energy"},
                    {"calculation_key": "product0_sp", "role": "product_energy"},
                    {"calculation_key": "ts_sp", "role": "ts_energy"},
                    {"calculation_key": "ts_freq", "role": "freq"},
                ],
            }
        ],
    }
    sp, _ = _write_computed_reaction_sidecar(tmp_path, payload=payload)

    recorder = _FactoryRecorder()
    summary = replay_bundle(tmp_path, client_factory=_make_factory(recorder))

    assert summary.uploaded == 1
    posted = recorder.calls[0].json
    assert posted == payload
    # Order and contents of source_calculations preserved exactly.
    sources = posted["kinetics"][0]["source_calculations"]
    assert [(s["calculation_key"], s["role"]) for s in sources] == [
        ("reactant0_sp", "reactant_energy"),
        ("product0_sp", "product_energy"),
        ("ts_sp", "ts_energy"),
        ("ts_freq", "freq"),
    ]


def test_computed_reaction_saved_payload_preserves_new_fields_on_disk(
    tmp_path: Path,
) -> None:
    """Sidecar + payload bundle written via the test helper round-trips
    through json.dumps/json.loads with the new fields intact. The
    'producer wrote, replay reads, replay POSTs' chain only works if
    on-disk JSON is preserving these keys verbatim.
    """
    payload = {
        "species": [],
        "transition_state": {
            "calculations": [
                {
                    "key": "ts_freq",
                    "type": "freq",
                    "input_geometries": [{"xyz_text": "1\n\nH 0 0 0"}],
                    "output_geometries": [
                        {"geometry": {"xyz_text": "1\n\nH 0 0 0"}, "role": "final"}
                    ],
                    "depends_on": [
                        {"parent_calculation_key": "ts_opt", "role": "freq_on"}
                    ],
                }
            ],
        },
        "kinetics": [
            {
                "source_calculations": [
                    {"calculation_key": "ts_sp", "role": "ts_energy"},
                ],
            }
        ],
    }
    _, payload_path = _write_computed_reaction_sidecar(tmp_path, payload=payload)

    on_disk = json.loads(payload_path.read_text())
    assert on_disk == payload
    # New fields are present on disk under the exact keys the backend expects.
    assert "input_geometries" in on_disk["transition_state"]["calculations"][0]
    assert "output_geometries" in on_disk["transition_state"]["calculations"][0]
    assert "depends_on" in on_disk["transition_state"]["calculations"][0]
    assert "source_calculations" in on_disk["kinetics"][0]


def test_computed_reaction_http_failure_marks_failed(tmp_path: Path) -> None:
    """A 500 from the server flips the sidecar to ``failed`` with the
    error captured — same semantics as computed_species."""
    sp, _ = _write_computed_reaction_sidecar(tmp_path)
    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=_raise_500),
    )
    assert summary.failed == 1
    updated = _read_sidecar(sp)
    assert updated["status"] == "failed"
    assert updated["response_status_code"] == 500
    assert "HTTP 500" in updated["last_error"]


def test_computed_reaction_dry_run_no_post(tmp_path: Path) -> None:
    """Dry-run discovers the sidecar but neither POSTs nor mutates it."""
    sp, _ = _write_computed_reaction_sidecar(tmp_path)
    pre = _read_sidecar(sp)

    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder),
        dry_run=True,
    )

    assert summary.dry_run == 1
    assert summary.by_kind["computed_reaction"]["dry_run"] == 1
    assert recorder.calls == []
    assert _read_sidecar(sp) == pre


# ---------------------------------------------------------------------------
# Tests — needs_regeneration terminal status for stale calculation_artifact 404
# ---------------------------------------------------------------------------


def _raise_404_calc_not_found(client, method, path, body, idempotency_key):
    """Stub responder that mimics the server when the parent calc id
    no longer exists — exactly the error the operator hit after a DB
    reset/re-upload of a bundle."""
    raise TCKDBHTTPError(
        "not found",
        status_code=404,
        code="not_found",
        detail="Calculation not found.",
        response_json={"detail": "Calculation not found."},
        response_text=None,
        headers={},
    )


def _raise_500_server_error(client, method, path, body, idempotency_key):
    """5xx variant: should NOT be promoted to needs_regeneration. The
    contrast guards the dispatch so a transient server bug doesn't
    silently retire valid sidecars."""
    raise TCKDBHTTPError(
        "internal server error",
        status_code=500,
        code="server_error",
        detail="boom",
        response_json={"detail": "boom"},
        response_text=None,
        headers={},
    )


def test_artifact_404_marks_needs_regeneration(tmp_path: Path) -> None:
    """A 404 on calculation_artifact replay → terminal needs_regeneration
    status, persisted on the sidecar, counted under the new bucket. The
    sidecar's calculation_id is stale and only regeneration from
    output.yml can fix it; retrying the POST would just produce another
    404 forever."""
    from tckdb_client.replay import STATUS_NEEDS_REGENERATION

    sp, _ = _write_artifact_sidecar(tmp_path)
    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=_raise_404_calc_not_found),
    )
    # New terminal status persisted on the sidecar.
    sidecar = _read_sidecar(sp)
    assert sidecar["status"] == STATUS_NEEDS_REGENERATION
    assert sidecar["response_status_code"] == 404
    # The body detail lands in response_body (subject to bounded-storage);
    # the formatted last_error captures the HTTP status + code line.
    assert "404" in (sidecar.get("last_error") or "")
    assert sidecar.get("response_body") == {"detail": "Calculation not found."}
    # Counted under the new skipped_needs_regeneration bucket — NOT under
    # failed (which would re-surface every replay).
    assert summary.skipped_needs_regeneration == 1
    assert summary.failed == 0
    assert summary.by_kind["calculation_artifact"]["skipped_needs_regeneration"] == 1


def test_artifact_404_does_not_record_failure_for_breakdown(tmp_path: Path) -> None:
    """The terminal status replaces the failure row — we don't want a
    permanent red line in the failure breakdown for a sidecar the
    operator has been told to regenerate. Subsequent runs would skip it
    silently anyway, so the breakdown should match that."""
    _write_artifact_sidecar(tmp_path)
    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=_raise_404_calc_not_found),
    )
    assert summary.failures == ()


def test_needs_regeneration_skipped_silently_on_subsequent_walk(
    tmp_path: Path,
) -> None:
    """After the first walk marks the sidecar terminal, the next walk
    must skip without an HTTP call. This is the core of the fix — no
    more 404 cascades on every replay."""
    from tckdb_client.replay import STATUS_NEEDS_REGENERATION

    sp, _ = _write_artifact_sidecar(tmp_path, status=STATUS_NEEDS_REGENERATION)
    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=_raise_404_calc_not_found),
    )
    assert recorder.calls == []  # no HTTP traffic for terminal sidecars
    assert summary.skipped_needs_regeneration == 1
    assert summary.failed == 0
    assert summary.uploaded == 0
    # Sidecar untouched (timestamps, last_error, etc. all preserved).
    assert _read_sidecar(sp)["status"] == STATUS_NEEDS_REGENERATION


def test_artifact_non_404_failure_does_not_promote_to_needs_regeneration(
    tmp_path: Path,
) -> None:
    """5xx must stay as ``failed``. A transient backend bug should leave
    the sidecar retryable; only a 404-style "parent calc gone" warrants
    the terminal demotion."""
    sp, _ = _write_artifact_sidecar(tmp_path)
    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=_raise_500_server_error),
    )
    sidecar = _read_sidecar(sp)
    assert sidecar["status"] == "failed"
    assert summary.skipped_needs_regeneration == 0
    assert summary.failed == 1


def test_bundle_404_does_not_promote_to_needs_regeneration(tmp_path: Path) -> None:
    """Only ``calculation_artifact`` 404s are terminal. A 404 from a
    bundle endpoint (computed_species / computed_reaction / conformer)
    is just a real upload failure — usually a server-side route
    mismatch — and must stay retryable so the operator can investigate
    or fix the deployment."""
    sp, _ = _write_conformer_sidecar(tmp_path)
    recorder = _FactoryRecorder()
    summary = replay_bundle(
        tmp_path,
        client_factory=_make_factory(recorder, responder=_raise_404_calc_not_found),
    )
    sidecar = _read_sidecar(sp)
    assert sidecar["status"] == "failed"
    assert summary.skipped_needs_regeneration == 0
    assert summary.failed == 1
