"""The /status endpoint, and the component signals behind it.

Stage 5's exit criterion includes "worker crash self-heals". Before this,
nothing anywhere reported whether the worker was running: ``readyz`` covered
the database and the schema revision only, so a dead worker left the API
answering 200 while ``/jobs/*`` accepted uploads that would never be
processed. That is the failure these tests are about.

The artifact-storage tests below are about the same class of failure, found
the expensive way. On 2026-08-05 a containerised deployment inherited
``S3_ENDPOINT_URL=http://127.0.0.1:9000`` from a host deployment, where
inside a container that address is the container's own loopback. Every
artifact-bearing upload returned 503 while ``/status`` reported fully
healthy, the dead man's switch stayed silent, and the deploy script's
post-deploy check -- which waited for ``status:ok`` -- passed throughout.
An endpoint that omits a hard dependency does not merely fail to report an
outage; it actively vouches for one.
"""

from __future__ import annotations

import threading
import time

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy.orm import sessionmaker

from app.api.routes import health
from app.services import artifact_storage

PROBE_ENDPOINT = "http://minio.test:9000"
PROBE_BUCKET = "tckdb-artifacts-test"


@pytest.fixture(autouse=True)
def _bind_status_probes_to_test_database(db_engine, monkeypatch):
    """Probe the migrated pytest database, not whatever ``DB_NAME`` names.

    ``/status`` reports the database as unhealthy when ``alembic_version``
    is missing, and it reaches that table through ``SessionLocal`` -- the
    module-level factory bound at import to ``settings.database_url``, i.e.
    to the ambient ``DB_NAME``, which is *not* the per-worker database this
    suite creates and migrates.

    That binding is what made these five tests assert on a database no
    fixture owned: a developer shell inherited ``tckdb_dev`` and passed, the
    PR gate ran ``alembic upgrade head`` against its ``DB_NAME`` in an
    earlier workflow step and passed, and the nightly -- which has no such
    step -- failed five tests every night for a reason that was nowhere in
    this file.

    **``tests/conftest.py`` now rebinds the ambient factory to the pytest
    database for the whole session**, so this fixture is no longer load
    bearing: these tests pass without it, including with ``DB_NAME`` naming a
    database that has never been created. It stays because the tests below
    that want a *specific* probe outcome monkeypatch ``health.SessionLocal``
    again in their own body, and an explicit baseline is what makes "wins
    here and is undone first" true rather than incidental.
    """
    monkeypatch.setattr(
        health, "SessionLocal", sessionmaker(bind=db_engine, expire_on_commit=False)
    )


class _FakeS3:
    """Stands in for the boto3 client; ``head_bucket`` does whatever the test says."""

    def __init__(self, effect) -> None:
        self._effect = effect
        self.calls: list[str] = []

    def head_bucket(self, Bucket: str):  # boto3 kwarg casing
        self.calls.append(Bucket)
        if isinstance(self._effect, BaseException):
            raise self._effect
        if callable(self._effect):
            return self._effect(Bucket)
        return {}


@pytest.fixture
def storage_probe(monkeypatch):
    """Point the probe at a fake object store with a known endpoint/bucket."""
    monkeypatch.setattr(artifact_storage, "S3_ENDPOINT_URL", PROBE_ENDPOINT)
    monkeypatch.setattr(artifact_storage, "S3_BUCKET", PROBE_BUCKET)

    def _install(effect) -> _FakeS3:
        fake = _FakeS3(effect)
        monkeypatch.setattr(
            artifact_storage, "_get_s3_client", lambda config=None: fake
        )
        return fake

    return _install


@pytest.fixture
def healthy_storage(storage_probe) -> None:
    """Default the storage component to healthy.

    Tests about *other* components must not depend on whether a MinIO
    happens to be running on the machine executing them.
    """
    storage_probe(None)


def test_status_reports_ok_when_every_component_is_healthy(
    client, healthy_storage
) -> None:
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["degraded"] == []
    assert body["components"]["database"]["healthy"] is True
    assert body["components"]["database"]["alembic_revision"]


def test_status_reports_the_database_server_encoding(
    client, healthy_storage
) -> None:
    """Answerable from outside, which is what makes it a five-second fix.

    A SQL_ASCII cluster stores bytes and validates nothing, and the
    damage is delayed arbitrarily -- everything works until the first
    non-ASCII character arrives. One em dash in a warning message rolled
    back an entire upload on 2026-08-04, months after the volume was
    created, and nothing anywhere said "your database is SQL_ASCII".

    It is reported and not judged. ``healthy`` stays ``revision is not
    None``, so the encoding can never flip the deployment to degraded:
    it is a permanent property of the cluster that only a dump and
    restore can change, and alerting on it would nag every five minutes
    about something no restart can address.
    """
    body = client.get("/api/v1/status").json()
    database = body["components"]["database"]
    assert database["server_encoding"], "a reachable database must report one"
    assert body["status"] == "ok"
    assert body["degraded"] == [], (
        "the encoding is reported, never a reason to degrade"
    )


def test_status_returns_200_even_when_degraded(
    client, monkeypatch, healthy_storage
) -> None:
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


# ---------------------------------------------------------------------------
# Artifact storage
# ---------------------------------------------------------------------------


def test_healthy_storage_is_reported_healthy(client, storage_probe) -> None:
    fake = storage_probe(None)

    response = client.get("/api/v1/status")

    assert response.status_code == 200
    body = response.json()
    block = body["components"]["artifact_storage"]
    assert block["healthy"] is True
    assert block["reachable"] is True
    assert block["reason"] is None
    assert body["degraded"] == []
    # A HEAD against the configured bucket — not a write, and not a round
    # trip. /status must not create load or leave objects behind.
    assert fake.calls == [PROBE_BUCKET]


def test_status_reports_which_endpoint_and_bucket_it_probed(
    client, storage_probe
) -> None:
    """The one fact that would have made the 2026-08-05 incident obvious.

    Every value in that deployment's config was individually valid; the
    only way to see the problem was to see *which address* the API was
    actually reaching for.
    """
    storage_probe(None)

    block = client.get("/api/v1/status").json()["components"]["artifact_storage"]

    assert block["endpoint"] == PROBE_ENDPOINT
    assert block["bucket"] == PROBE_BUCKET


def test_unreachable_endpoint_degrades_without_a_5xx(client, storage_probe) -> None:
    """The incident, reproduced: storage unreachable, everything else fine.

    ``/status`` must answer 200 and say what is wrong. A 5xx here would be
    indistinguishable from the outage it is describing.
    """
    storage_probe(EndpointConnectionError(endpoint_url=PROBE_ENDPOINT))

    response = client.get("/api/v1/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert "artifact_storage" in body["degraded"]
    block = body["components"]["artifact_storage"]
    assert block["healthy"] is False
    assert block["reachable"] is False
    assert "cannot reach" in block["reason"]
    assert block["endpoint"] == PROBE_ENDPOINT
    # The database is untouched by a storage outage, which is exactly why
    # the old endpoint reported the deployment as healthy.
    assert body["components"]["database"]["healthy"] is True


@pytest.mark.parametrize(
    "code,expected_fragment",
    [
        ("404", "does not exist"),
        ("NoSuchBucket", "does not exist"),
        ("403", "refused"),
        ("AccessDenied", "refused"),
        ("InvalidAccessKeyId", "refused"),
    ],
)
def test_bucket_problems_are_distinguished_from_an_unreachable_endpoint(
    client, storage_probe, code, expected_fragment
) -> None:
    """Reached-but-refused is a different fix from cannot-reach.

    Collapsing them sends an operator to check the network when the answer
    is a bucket name or a key pair. The ``database`` component splits
    unreachable from schema-not-initialised for the same reason.
    """
    storage_probe(
        ClientError({"Error": {"Code": code, "Message": "nope"}}, "HeadBucket")
    )

    block = client.get("/api/v1/status").json()["components"]["artifact_storage"]

    assert block["healthy"] is False
    # The endpoint answered. That is the distinction.
    assert block["reachable"] is True
    assert expected_fragment in block["reason"]
    assert PROBE_BUCKET in block["reason"]


def test_status_answers_promptly_when_storage_hangs(client, storage_probe) -> None:
    """A dead endpoint must degrade /status, never block it.

    botocore's own timeouts do not cover every stall (DNS is the usual
    one), so the probe is bounded outside botocore. If ``/status`` hung
    instead, a checker would read the timeout as "host down" — the wrong
    page of the runbook — and the alert would be about the wrong thing.
    """
    release = threading.Event()

    def _hang(_bucket):
        release.wait(timeout=60)
        return {}

    storage_probe(_hang)

    started = time.monotonic()
    try:
        response = client.get("/api/v1/status")
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert response.status_code == 200
    assert elapsed < health._STORAGE_PROBE_DEADLINE_SECONDS + 5
    body = response.json()
    assert body["status"] == "degraded"
    assert "artifact_storage" in body["degraded"]
    block = body["components"]["artifact_storage"]
    assert block["reachable"] is False
    assert "did not respond" in block["reason"]
    # Reported even when the probe never came back: an operator staring at
    # a timeout still needs to know what was being reached for.
    assert block["endpoint"] == PROBE_ENDPOINT


def test_the_probe_deadline_is_short_enough_to_poll(client) -> None:
    """/status is polled every 5 minutes; it must not cost seconds to answer."""
    assert health._STORAGE_PROBE_DEADLINE_SECONDS <= 5.0
    assert health._STORAGE_PROBE_CONNECT_TIMEOUT <= 2.0
    assert health._STORAGE_PROBE_READ_TIMEOUT <= 2.0


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://127.0.0.1:9000", "http://127.0.0.1:9000"),
        ("https://s3.example.com", "https://s3.example.com"),
        ("http://minio:9000/some/path?x=1", "http://minio:9000"),
        # /status is public. A credential pasted into the endpoint URL must
        # not ride out on it.
        ("http://key:secret@minio:9000", "http://minio:9000"),
        ("", "(unset)"),
        ("not a url", "(unparseable)"),
    ],
)
def test_reported_endpoint_is_reduced_to_scheme_host_port(raw, expected) -> None:
    assert health._sanitized_endpoint(raw) == expected
