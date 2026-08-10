"""The object store is probed at boot, and says so where operators look.

On 2026-08-05 a containerised deployment ran for hours with
``S3_ENDPOINT_URL`` pointing at the container's own loopback. Every
artifact-bearing upload returned 503; nothing in the boot log mentioned
it, because nothing looked. The misconfiguration was fully detectable
from the first second of the process's life.

These tests pin the three properties that make the probe worth having,
and each one is a way the previous version got it wrong: it runs by
default, it names what it tried to reach, and it never takes the process
down with it.
"""

from __future__ import annotations

import logging
import os

import pytest
from fastapi.testclient import TestClient

from app.api import startup_checks
from app.api.app import create_app

UNREACHABLE = {
    "endpoint": "http://127.0.0.1:9000",
    "bucket": "tckdb-artifacts",
    "healthy": False,
    "reachable": False,
    "reason": "cannot reach object store (EndpointConnectionError)",
}

REACHABLE = {
    "endpoint": "http://minio:9000",
    "bucket": "tckdb-artifacts",
    "healthy": True,
    "reachable": True,
    "reason": None,
}


@pytest.fixture
def probe_enabled(monkeypatch):
    """Undo the suite-wide opt-out for this module only.

    The encoding probe is stubbed out alongside it, so the storage
    assertions below are not at the mercy of whatever encoding the
    developer's local cluster happens to have. (Several are ``SQL_ASCII``
    — that is the whole reason the encoding probe exists — and it would
    otherwise put a genuine ERROR record into every ``caplog``.)
    """
    monkeypatch.setenv("TCKDB_STARTUP_PROBES", "true")
    monkeypatch.setattr(
        "app.api.app.report_database_encoding_at_startup", lambda: None
    )


def _patch_probe(monkeypatch, outcome):
    calls = []

    def fake_status():
        calls.append(True)
        return dict(outcome)

    monkeypatch.setattr(
        "app.api.routes.health._artifact_storage_status", fake_status
    )
    return calls


def test_probe_runs_on_startup_by_default(probe_enabled, monkeypatch):
    """No env var needed. The deployment that most needs this is the one
    nobody remembered to configure."""
    monkeypatch.delenv("TCKDB_STARTUP_PROBES", raising=False)
    assert "TCKDB_STARTUP_PROBES" not in os.environ
    calls = _patch_probe(monkeypatch, REACHABLE)
    with TestClient(create_app()):
        pass
    assert len(calls) == 1


def test_unreachable_storage_is_logged_at_error_with_the_endpoint(
    probe_enabled, monkeypatch, caplog
):
    """The endpoint has to be in the message.

    The whole content of the incident was that the address was wrong
    while every individual value in the configuration was still valid.
    A verdict without the address sends an operator to read source.
    """
    _patch_probe(monkeypatch, UNREACHABLE)
    with caplog.at_level(logging.INFO, logger="app.api.startup_checks"):
        with TestClient(create_app()):
            pass

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "an unreachable object store must be an ERROR, not a shrug"
    message = errors[0].getMessage()
    assert "127.0.0.1:9000" in message
    assert "tckdb-artifacts" in message
    assert "EndpointConnectionError" in message


def test_healthy_storage_is_logged_too(probe_enabled, monkeypatch, caplog):
    """A silent success is indistinguishable from a probe that never ran."""
    _patch_probe(monkeypatch, REACHABLE)
    with caplog.at_level(logging.INFO, logger="app.api.startup_checks"):
        with TestClient(create_app()):
            pass
    messages = [r.getMessage() for r in caplog.records]
    assert any("artifact storage ok" in m for m in messages)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_unreachable_storage_does_not_prevent_startup(probe_enabled, monkeypatch):
    """Reads and queries still work with the object store down.

    Refusing to boot would turn a partial outage into a total one --
    the same reasoning that keeps artifact storage out of ``/readyz``.
    """
    _patch_probe(monkeypatch, UNREACHABLE)
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_a_probe_that_raises_does_not_take_the_process_with_it(
    probe_enabled, monkeypatch, caplog
):
    """Boot diagnostics exist to make failures visible, not to create one."""

    def boom():
        raise RuntimeError("botocore exploded in a new way")

    monkeypatch.setattr("app.api.routes.health._artifact_storage_status", boom)
    with caplog.at_level(logging.INFO, logger="app.api.startup_checks"):
        with TestClient(create_app()) as client:
            assert client.get("/api/v1/health").status_code == 200
    assert any("probe raised RuntimeError" in r.getMessage() for r in caplog.records)


def test_opt_out_is_honoured(monkeypatch):
    monkeypatch.setenv("TCKDB_STARTUP_PROBES", "false")
    calls = _patch_probe(monkeypatch, REACHABLE)
    with TestClient(create_app()):
        pass
    assert calls == []


def test_report_returns_the_same_block_status_reports(probe_enabled, monkeypatch):
    """One probe definition, so /status and the boot log cannot drift."""
    _patch_probe(monkeypatch, UNREACHABLE)
    assert startup_checks.report_artifact_storage_at_startup() == UNREACHABLE


# ---------------------------------------------------------------------------
# Database encoding
# ---------------------------------------------------------------------------


def _patch_encoding(monkeypatch, value):
    monkeypatch.setattr(
        "app.api.startup_checks.server_encoding", lambda session: value
    )


def test_a_non_utf8_cluster_is_an_error_at_boot(monkeypatch, caplog):
    """The failure this makes visible is delayed and looks unrelated.

    A SQL_ASCII cluster works perfectly until the first non-ASCII byte
    arrives, which may be months after the volume was created. One em
    dash in a warning message rolled back an entire upload on
    2026-08-04, and nothing anywhere said "your database is SQL_ASCII".
    """
    _patch_encoding(monkeypatch, "SQL_ASCII")
    with caplog.at_level(logging.INFO, logger="app.api.startup_checks"):
        assert startup_checks.report_database_encoding_at_startup() == "SQL_ASCII"

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a cluster that validates nothing is not a debug-level fact"
    message = errors[0].getMessage()
    assert "SQL_ASCII" in message
    assert "UTF8" in message
    # The fix is a dump and restore, not a restart. Saying so is the
    # difference between a useful log line and an alarming one.
    assert "initdb" in message


def test_a_utf8_cluster_is_recorded_without_alarm(monkeypatch, caplog):
    _patch_encoding(monkeypatch, "UTF8")
    with caplog.at_level(logging.INFO, logger="app.api.startup_checks"):
        assert startup_checks.report_database_encoding_at_startup() == "UTF8"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "server_encoding=UTF8" in r.getMessage() for r in caplog.records
    )


def test_an_unreachable_database_at_boot_is_not_a_fault(monkeypatch, caplog):
    """The API may legitimately start before Postgres accepts connections."""
    _patch_encoding(monkeypatch, None)
    with caplog.at_level(logging.DEBUG, logger="app.api.startup_checks"):
        assert startup_checks.report_database_encoding_at_startup() is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_encoding_probe_runs_at_startup(monkeypatch):
    monkeypatch.setenv("TCKDB_STARTUP_PROBES", "true")
    monkeypatch.setattr(
        "app.api.app.report_artifact_storage_at_startup", lambda: {}
    )
    seen = []
    monkeypatch.setattr(
        "app.api.app.report_database_encoding_at_startup",
        lambda: seen.append(True),
    )
    with TestClient(create_app()):
        pass
    assert seen == [True]


def test_status_reports_the_server_encoding(client):
    """Answerable from outside, which is what makes it a five-second fix.

    It is reported and not judged: a non-UTF8 cluster is a permanent
    property that only a dump and restore can change, so degrading
    /status on it would nag every five minutes about something no
    restart can address.
    """
    body = client.get("/api/v1/status").json()
    database = body["components"]["database"]
    assert "server_encoding" in database
    assert database["server_encoding"], "a reachable database must report one"
    assert body["status"] == "ok"
    assert body["degraded"] == []
