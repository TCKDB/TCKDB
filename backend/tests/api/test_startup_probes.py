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
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.api import startup_checks
from app.api.app import create_app
from app.api.config import Settings

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


# `/status` reporting server_encoding is asserted in tests/api/test_api_status.py,
# beside the other component-shape tests and on top of that module's stubbed
# object store. Asserting the whole body here would have made this module's
# result depend on whether a real MinIO bucket happened to exist yet.


# ---------------------------------------------------------------------------
# The probe has a deadline, and the failure it needs one for is not a refusal
# ---------------------------------------------------------------------------


@pytest.fixture
def black_holed_port():
    """An address that accepts packets and drops them, without root.

    The distinction this fixture exists to make is the whole of the bug.
    A database host that is *down* refuses the connection and libpq
    returns in under a millisecond, which is why every previous test of
    this probe passed instantly while the probe was in fact unbounded. A
    host that is black-holed -- a firewall rule, a departed container, a
    security group, a stale DNS answer pointing at a live subnet --
    silently drops the SYN, and the caller then waits out the kernel's
    SYN retries: measured at 130 s on this stack.

    Reproduced here by filling a listening socket's accept queue.
    Linux drops further SYNs rather than resetting them once the queue
    is full (``tcp_abort_on_overflow=0``, the default), so a connect to
    this port hangs and retries exactly as it would against a filtered
    host. No privileges, no external network, no sleep in the test.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()
    fillers = []
    for _ in range(32):
        filler = socket.socket()
        filler.setblocking(False)
        filler.connect_ex((host, port))
        fillers.append(filler)
    # Let the kernel complete the handshakes it has room for; everything
    # after that is dropped, which is the state being borrowed.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        probe = socket.socket()
        probe.settimeout(0.25)
        try:
            probe.connect((host, port))
        except (TimeoutError, socket.timeout):
            break
        except OSError:  # pragma: no cover - defensive
            break
        finally:
            probe.close()
    try:
        yield host, port
    finally:
        for filler in fillers:
            filler.close()
        listener.close()


def _closed_port() -> tuple[str, int]:
    """An address where a connection is actively refused."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    host, port = probe.getsockname()
    probe.close()
    return host, port


def test_the_engine_url_carries_a_connect_timeout():
    """Without it, nothing bounds the socket connect itself.

    ``db_statement_timeout_ms`` is server-side and so bounds nothing
    until there is a server on the far end of a socket, which is exactly
    the case that hangs.
    """
    assert "connect_timeout=10" in Settings().database_url


def test_a_zero_connect_timeout_is_omitted_rather_than_sent_as_zero():
    """``connect_timeout=0`` means "no timeout" to libpq, not "instant"."""
    assert "connect_timeout" not in Settings(db_connect_timeout_seconds=0).database_url


def test_a_black_holed_database_host_gives_up_instead_of_hanging(black_holed_port):
    """The measurement, against the failure mode that actually hangs.

    Unbounded, this same connection took 130 s to fail. The assertion is
    not that it fails -- it always did -- but that it fails while anyone
    is still watching.
    """
    host, port = black_holed_port
    url = Settings(
        db_host=host, db_port=port, db_connect_timeout_seconds=2
    ).database_url
    outcome: dict[str, object] = {}

    def connect() -> None:
        started = time.monotonic()
        try:
            with create_engine(url).connect() as connection:
                connection.execute(text("SELECT 1"))
            outcome["result"] = "connected"
        except Exception as exc:
            outcome["result"] = type(exc).__name__
        outcome["elapsed"] = time.monotonic() - started

    thread = threading.Thread(target=connect, daemon=True)
    thread.start()
    thread.join(20)

    assert not thread.is_alive(), (
        "the connect is unbounded: this is the 130s hang, and a test that "
        "waits for it is a test nobody runs"
    )
    assert outcome["result"] == "OperationalError"
    # Above the connect timeout, which is what proves the fixture served a
    # black hole and not a refusal -- a refused connection returns in
    # microseconds and would pass the upper bound while proving nothing.
    assert 1.5 < outcome["elapsed"] < 15


def test_the_encoding_probe_stops_waiting_for_a_database_that_never_answers(
    monkeypatch, caplog
):
    """A connected-but-mute server escapes ``connect_timeout`` entirely.

    libpq's timeout covers establishing the connection. A host that
    completes the handshake and then never answers ``SHOW
    server_encoding`` -- a wedged cluster, a proxy holding the socket
    open -- is past it, and ``statement_timeout`` cannot help because
    setting it requires the same server to answer. The wall-clock
    deadline is the only thing that bounds this shape, so it is asserted
    against this shape.
    """
    released = threading.Event()
    monkeypatch.setattr(startup_checks, "_ENCODING_PROBE_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(
        startup_checks,
        "_read_server_encoding",
        lambda: (released.wait(30), "UTF8")[1],
    )

    started = time.monotonic()
    try:
        with caplog.at_level(logging.DEBUG, logger="app.api.startup_checks"):
            assert startup_checks.report_database_encoding_at_startup() is None
        elapsed = time.monotonic() - started
    finally:
        released.set()

    assert elapsed < 5
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "abandoning the check silently is how it stayed invisible"
    assert "did not answer" in warnings[0].getMessage()


def test_the_shipped_deadline_is_small_enough_to_be_a_bound():
    """A deadline longer than an operator's patience is not a deadline.

    Pinned beside the storage probe's 4 s: the two together are the
    worst case a pair of dead dependencies can add to boot, and that
    total is the number ``app.api.app``'s lifespan docstring promises.
    """
    assert 0 < startup_checks._ENCODING_PROBE_DEADLINE_SECONDS <= 10


def test_a_refused_connection_remains_the_fast_quiet_path(monkeypatch, caplog):
    """The two absences are different and only one of them was ever slow.

    A refused connection is the normal boot race -- the API starting
    before Postgres accepts connections -- and must stay a ``debug``
    line rather than inheriting the black-hole warning.
    """
    host, port = _closed_port()
    engine = create_engine(Settings(db_host=host, db_port=port).database_url)
    monkeypatch.setattr(
        startup_checks, "_read_server_encoding", lambda: _encoding_via(engine)
    )

    started = time.monotonic()
    with caplog.at_level(logging.DEBUG, logger="app.api.startup_checks"):
        assert startup_checks.report_database_encoding_at_startup() is None
    elapsed = time.monotonic() - started

    assert elapsed < 2
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _encoding_via(engine) -> str | None:
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        return startup_checks.server_encoding(session)
