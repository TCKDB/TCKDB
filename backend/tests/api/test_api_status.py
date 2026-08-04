"""The /status endpoint, and the worker-liveness signal behind it.

Stage 5's exit criterion includes "worker crash self-heals". Before this,
nothing anywhere reported whether the worker was running: ``readyz`` covered
the database and the schema revision only, so a dead worker left the API
answering 200 while ``/jobs/*`` accepted uploads that would never be
processed. That is the failure these tests are about.
"""

from __future__ import annotations

import threading

import pytest

from app.api.routes import health


def test_status_reports_ok_when_every_component_is_healthy(client) -> None:
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["degraded"] == []
    assert body["components"]["database"]["healthy"] is True
    assert body["components"]["database"]["alembic_revision"]


def test_status_returns_200_even_when_degraded(client, monkeypatch) -> None:
    """A degraded system must still be able to say so.

    Returning 5xx here would make the endpoint indistinguishable from the
    outage it describes: a checker could not tell "the site is down" from
    "the site is up and reporting a dead worker", and those are different
    pages of the runbook.
    """
    monkeypatch.setattr(
        health,
        "_worker_status",
        lambda: {"healthy": False, "reason": "inline worker thread is not running"},
    )
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["degraded"] == ["worker"]
    assert "worker" in body["components"]


def test_inline_worker_with_no_live_thread_is_unhealthy(monkeypatch, db_session) -> None:
    """The silent failure: inline worker configured, thread gone.

    An empty queue must not make this look fine. ``upload_job.heartbeat_at``
    is written only while a job is being processed, so an idle worker and a
    dead one are identical by heartbeat — which is why the thread is checked
    directly.
    """
    monkeypatch.setenv("TCKDB_INLINE_WORKER", "true")
    monkeypatch.setattr(threading, "enumerate", lambda: [])

    status = health._worker_status()

    assert status["inline"] is True
    assert status["thread_alive"] is False
    assert status["healthy"] is False
    assert "not running" in status["reason"]


def test_inline_worker_with_a_live_thread_is_healthy(monkeypatch, db_session) -> None:
    monkeypatch.setenv("TCKDB_INLINE_WORKER", "true")

    started = threading.Event()
    stop = threading.Event()

    def _park() -> None:
        started.set()
        stop.wait(timeout=10)

    worker = threading.Thread(target=_park, name=health._WORKER_THREAD_NAME, daemon=True)
    worker.start()
    started.wait(timeout=5)
    try:
        status = health._worker_status()
    finally:
        stop.set()
        worker.join(timeout=5)

    assert status["thread_alive"] is True
    assert status["healthy"] is True
    assert status["reason"] is None


def test_a_separate_process_worker_is_not_judged_by_thread_liveness(
    monkeypatch, db_session
) -> None:
    """Thread inspection cannot see another process, so it must not be used.

    With ``TCKDB_INLINE_WORKER`` unset the API is not supposed to be running
    a worker, and the absence of the thread is correct rather than broken.
    Queue lag is the signal that still applies to that deployment.
    """
    monkeypatch.delenv("TCKDB_INLINE_WORKER", raising=False)
    monkeypatch.setattr(threading, "enumerate", lambda: [])

    status = health._worker_status()

    assert status["inline"] is False
    assert status["thread_alive"] is None
    assert status["healthy"] is True


@pytest.mark.parametrize("age_seconds,expected_stalled", [(10, False), (10_000, True)])
def test_queue_lag_flags_a_worker_that_is_not_claiming_work(
    monkeypatch, db_session, age_seconds, expected_stalled
) -> None:
    """Queue lag catches a dead worker of any deployment shape.

    It only says anything when there *is* work, which is why it does not
    replace the thread check — on this deployment the queue is normally
    empty.
    """
    from datetime import datetime, timedelta, timezone

    monkeypatch.delenv("TCKDB_INLINE_WORKER", raising=False)

    class _Row:
        queued = 1
        oldest = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)

    class _Result:
        def one(self):
            return _Row()

    class _Session:
        def execute(self, *_args, **_kwargs):
            return _Result()

        def close(self):
            pass

    monkeypatch.setattr(health, "SessionLocal", lambda: _Session())

    status = health._worker_status()

    assert status["queued"] == 1
    assert status["queue_stalled"] is expected_stalled
    assert status["healthy"] is not expected_stalled
