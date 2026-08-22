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
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api import deps as api_deps
from app.api.app import create_app
from app.api.routes import health
from app.db.models.artifact_storage_capacity import ArtifactStorageCapacityEvent
from app.services import (
    artifact_storage,
    artifact_storage_admin,
    artifact_storage_headroom,
)
from app.services import artifact_storage_capacity as capacity

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


def test_status_reports_the_cluster_template_encoding(
    client, healthy_storage
) -> None:
    """The same hazard one step earlier than ``server_encoding``.

    ``server_encoding`` describes the database that exists. ``template1``
    describes the one the next database created on this cluster inherits
    from, and the two can disagree: converting an application database in
    place fixes it and leaves the cluster's templates alone. The live
    deployment held a ``UTF8`` ``tckdb`` beside a ``SQL_ASCII`` ``template1``
    when checked on 2026-08-12, eight days after the conversion.

    That matters because every restore runbook drops and recreates the
    database before loading the dump, so the template's encoding becomes the
    production encoding at exactly the moment nobody is looking -- and a
    ``SQL_ASCII`` database accepts a ``UTF8`` dump without a single error.

    Reported, never judged, on the same reasoning as ``server_encoding``.
    """
    body = client.get("/api/v1/status").json()
    database = body["components"]["database"]
    assert database["template_encoding"], (
        "a reachable cluster must report its template1 encoding"
    )
    assert body["status"] == "ok"
    assert body["degraded"] == [], (
        "a divergent template is reported, never a reason to degrade"
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


# ---------------------------------------------------------------------------
# A full store, which the read-only probe cannot see
# ---------------------------------------------------------------------------


@pytest.fixture
def capacity_db(db_engine, monkeypatch):
    """One connection that the capacity log and ``/status`` both read.

    The observation used to be a module global a fixture could reset. It
    is a table now, so isolation is a transaction: a dedicated connection
    whose outer transaction is rolled back at teardown, with both the
    ambient factory and ``/status``'s bound to it.

    This deliberately re-patches ``health.SessionLocal`` after
    ``_bind_status_probes_to_test_database`` has set it, because that
    fixture binds to the *engine* -- a fresh pooled connection that would
    see only committed rows, and nothing here commits.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    connection.begin_nested()
    factory = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(api_deps, "SessionLocal", factory)
    monkeypatch.setattr(health, "SessionLocal", factory)
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _no_capacity_probe_by_default(monkeypatch):
    """No test reaches a real MinIO admin API unless it says to.

    Default is "no opinion" from **both** admin probes, which is what a
    non-MinIO store answers: it leaves a recorded refusal untouched and
    leaves the headroom report with no arms, so no warning is raised.

    The quota cache is cleared around every test. It is module-level and
    keyed by ``(endpoint, bucket)``, both of which are constant across this
    file -- so without this, the first test to set a quota would set it for
    every test scheduled after it, and ``pytest-randomly`` would make that
    a different set every run.
    """
    artifact_storage_headroom.clear_quota_cache()
    monkeypatch.setattr(
        artifact_storage_admin, "report_free_bytes", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        artifact_storage_admin, "report_bucket_quota_bytes", lambda **_kwargs: None
    )
    yield
    artifact_storage_headroom.clear_quota_cache()


def _observe_a_full_store(factory, attempted_bytes: int = 4_194_304) -> None:
    """Record what the write path records when the store refuses for room.

    Written through the production recorder rather than by inserting a row
    by hand, so a test cannot pass against a record shape the write path
    does not produce.
    """
    capacity.record_refusal(
        s3_code="XMinioStorageFull",
        attempted_bytes=attempted_bytes,
        detail=(
            "Artifact storage write failed for sha=deadbeef: "
            "Storage backend has reached its minimum free drive threshold."
        ),
        session_factory=factory,
    )


def test_the_read_only_probe_cannot_see_a_full_store(client, storage_probe) -> None:
    """The negative finding, pinned so nobody re-derives it the hard way.

    Measured against MinIO ``RELEASE.2025-09-07T16-13-09Z`` on a volume
    filled to its free-space threshold: ``head_bucket`` returns 200, every
    read succeeds, and even a 1-byte write succeeds on the same store that
    refuses a 4 MiB one. So the probe ``/status`` performs is *incapable*
    of distinguishing a full store from a healthy one, and this asserts
    that plainly: with nothing but the probe to go on, a store that refuses
    every real upload reads healthy.

    That is what makes the observation below necessary. It is not a
    redundant second signal — it is the only signal there is.
    """
    fake = storage_probe(None)

    block = client.get("/api/v1/status").json()["components"]["artifact_storage"]

    # A HEAD is all that happened, and a HEAD is 200 on a full store.
    assert fake.calls == [PROBE_BUCKET]
    assert block["healthy"] is True
    assert block["storage_full"] is False


def test_a_full_store_degrades_status_and_says_what_to_do(
    client, storage_probe, capacity_db
) -> None:
    """The direction that was silent: green while every upload failed."""
    storage_probe(None)  # the probe itself is, and stays, happy
    _observe_a_full_store(capacity_db)

    response = client.get("/api/v1/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert "artifact_storage" in body["degraded"]
    block = body["components"]["artifact_storage"]
    assert block["healthy"] is False
    assert block["storage_full"] is True
    assert block["storage_full_observed_at"] is not None
    # The endpoint answered, so this is not an unreachability report and
    # must not be confused with one.
    assert block["reachable"] is True
    assert "want of room" in block["reason"], block["reason"]
    assert "XMinioStorageFull" in block["reason"], block["reason"]
    # Sized, so an operator can tell "nearly full" from "nothing fits".
    assert "4,194,304-byte" in block["reason"], block["reason"]
    # The other components are untouched -- which is exactly why nothing
    # alerted before.
    assert body["components"]["database"]["healthy"] is True


def test_a_working_store_is_healthy_again_once_a_big_enough_write_succeeds(
    client, storage_probe, capacity_db
) -> None:
    """The other direction, or the test above cannot tell the two apart.

    A flag that never clears is a stuck alarm, and an alarm that is always
    on is the same non-signal as one that is always off.
    """
    storage_probe(None)
    _observe_a_full_store(capacity_db, attempted_bytes=4_194_304)
    assert (
        client.get("/api/v1/status").json()["components"]["artifact_storage"][
            "healthy"
        ]
        is False
    )

    # A *small* write first: it must not be enough. Without this the test
    # below would pass against the naive "any success clears it" rule.
    capacity.note_successful_write(accepted_bytes=1, session_factory=capacity_db)
    assert (
        client.get("/api/v1/status").json()["components"]["artifact_storage"][
            "storage_full"
        ]
        is True
    ), "a 1-byte write turned /status green while 4 MiB was still refused"

    capacity.note_successful_write(
        accepted_bytes=4_194_304, session_factory=capacity_db
    )

    body = client.get("/api/v1/status").json()
    block = body["components"]["artifact_storage"]
    assert block["healthy"] is True
    assert block["storage_full"] is False
    assert block["storage_full_observed_at"] is None
    assert block["reason"] is None
    assert body["status"] == "ok"
    assert body["degraded"] == []


def test_a_full_store_that_is_also_unreachable_reports_both(
    client, storage_probe, capacity_db
) -> None:
    """Two true facts at once, and the actionable one leads.

    The capacity observation only ever removes health; it must never
    overwrite an unreachability report, because "cannot reach the endpoint"
    and "the endpoint has no room" send an operator to different places and
    the first has to be fixed first.
    """
    storage_probe(EndpointConnectionError(endpoint_url=PROBE_ENDPOINT))
    _observe_a_full_store(capacity_db)

    block = client.get("/api/v1/status").json()["components"]["artifact_storage"]

    assert block["healthy"] is False
    assert block["reachable"] is False
    assert "cannot reach" in block["reason"], block["reason"]
    assert "want of room" in block["reason"], block["reason"]
    assert block["storage_full"] is True


def test_the_probe_deadline_is_short_enough_to_poll(client) -> None:
    """/status is polled every 5 minutes; it must not cost seconds to answer."""
    assert health._STORAGE_PROBE_DEADLINE_SECONDS <= 5.0
    assert health._STORAGE_PROBE_CONNECT_TIMEOUT <= 2.0
    assert health._STORAGE_PROBE_READ_TIMEOUT <= 2.0
    # The capacity probe runs inline, *after* the bucket probe, so the two
    # budgets add. Asserted together because either one alone looks fine and
    # the sum is what an operator's poll actually waits for.
    assert artifact_storage_admin._TIMEOUT_SECONDS <= 2.0
    assert (
        health._STORAGE_PROBE_DEADLINE_SECONDS
        + artifact_storage_admin._TIMEOUT_SECONDS
    ) <= 6.0


# ---------------------------------------------------------------------------
# Surviving a restart, which is the whole reason this became a table
# ---------------------------------------------------------------------------


def test_status_reads_a_refusal_no_code_in_this_process_ever_saw(
    client, storage_probe, capacity_db
) -> None:
    """The assertion that actually proves durability.

    This is the discriminator, and the restart test below is *not*. A module
    global survives ``create_app()`` perfectly well — it lives in the module,
    not in the application object — so "a freshly built app still reports it"
    would have passed against the very implementation this change replaces.
    That test would have been green for a reason unrelated to what it claims.

    So the refusal is inserted **straight into the table**, by a plain ORM
    add, with no TCKDB code path notified: no recorder called, nothing
    latched, nothing in this process aware the store was ever refused. If
    ``/status`` reported anything but "full", it would be reading process
    state — which is exactly the defect.

    This is also what a second API worker, or the process after a restart,
    actually sees: a row somebody else wrote.
    """
    storage_probe(None)
    with capacity_db() as session:
        with session.begin():
            session.add(
                ArtifactStorageCapacityEvent(
                    observation=capacity.ArtifactStorageCapacityObservation.refused,
                    observed_bytes=4_194_304,
                    s3_code="XMinioStorageFull",
                    detail="written by nothing in this process",
                )
            )

    block = client.get("/api/v1/status").json()["components"]["artifact_storage"]

    assert block["storage_full"] is True, (
        "/status did not see a refusal it was never told about in memory: "
        "the fact is not being read from the database"
    )
    assert block["healthy"] is False
    assert "4,194,304-byte" in block["reason"], block["reason"]


def test_a_full_store_is_still_reported_after_a_restart(
    client, storage_probe, capacity_db
) -> None:
    """The defect this change exists to fix, end to end.

    The observation used to be a module global. A restart forgot it, and
    the API came back up reporting ``artifact_storage`` healthy while every
    upload still failed with 507 — the same confident all-clear over a live
    outage that the endpoint exists to prevent.

    "Restart" here is a **freshly constructed application object**. On its
    own that is weaker than it looks (see the test above, which is the real
    discriminator); it is asserted because it is the shape an operator
    recognises, and because the two together say the whole thing: the fact
    is in the database, and a new app instance serves it.
    """
    storage_probe(None)
    _observe_a_full_store(capacity_db)
    assert (
        client.get("/api/v1/status").json()["components"]["artifact_storage"][
            "storage_full"
        ]
        is True
    )

    restarted = TestClient(create_app())
    block = restarted.get("/api/v1/status").json()["components"]["artifact_storage"]

    assert block["storage_full"] is True, (
        "a restarted process reported a healthy store while a refusal stood"
    )
    assert block["healthy"] is False
    assert block["storage_full_observed_at"] is not None
    assert "want of room" in block["reason"], block["reason"]


def test_a_restart_does_not_invent_a_full_store(client, storage_probe) -> None:
    """The other half: with an empty log, a fresh process reports healthy.

    Without this, ``storage_full: true`` after a restart could be a
    constant rather than a reading, and the test above would pass against
    an implementation that always degrades.
    """
    storage_probe(None)
    restarted = TestClient(create_app())
    block = restarted.get("/api/v1/status").json()["components"]["artifact_storage"]
    assert block["storage_full"] is False
    assert block["healthy"] is True


# ---------------------------------------------------------------------------
# The free-space probe, which is what notices recovery without an upload
# ---------------------------------------------------------------------------


def test_a_capacity_report_of_enough_free_space_clears_it_on_status(
    client, storage_probe, capacity_db, monkeypatch
) -> None:
    """Recovery noticed by ``/status`` itself, not by the next depositor.

    The write path alone only learns the store recovered when someone
    happens to upload something large enough, which could be days. MinIO's
    admin API reports free space with the credentials this process already
    holds, so ``/status`` asks — but only while a refusal is outstanding.
    """
    storage_probe(None)
    _observe_a_full_store(capacity_db, attempted_bytes=4_194_304)

    # Still tight: measured, ``availspace`` was 4,030,464 at the instant a
    # 4,194,304-byte write was refused. Not enough is not enough.
    monkeypatch.setattr(
        artifact_storage_admin, "report_free_bytes", lambda **_kwargs: 4_030_464
    )
    block = client.get("/api/v1/status").json()["components"]["artifact_storage"]
    assert block["storage_full"] is True, (
        "a free-space number below the refused size cleared the report"
    )

    # An operator added a disk.
    monkeypatch.setattr(
        artifact_storage_admin, "report_free_bytes", lambda **_kwargs: 2 * 1024**3
    )
    block = client.get("/api/v1/status").json()["components"]["artifact_storage"]
    assert block["storage_full"] is False
    assert block["healthy"] is True


def test_a_healthy_poll_asks_once_and_records_nothing(
    client, storage_probe, capacity_db, monkeypatch
) -> None:
    """A healthy poll writes no row, and asks the store at most one question.

    THIS TEST CHANGED MEANING, deliberately, and the old meaning is worth
    stating because it was right at the time. It used to assert that a
    healthy store is *never* asked for its capacity: the free-space probe
    existed only to answer an outstanding refusal, so on a healthy store it
    was pure cost. Adding the "approaching full" warning makes the same
    round trip the only way to see a store filling up **before** a
    depositor is refused, so it now runs on every poll -- one call, not two,
    and the cheap half of the report (the ledger sum) is a local database
    aggregate.

    What was load-bearing about the old test survives intact and is now
    asserted rather than implied:

    * **No row is written.** "A row per poll would turn the log into a
      metric series" -- the capacity log records incidents, and a warning
      is not an incident. Counted directly against the table.
    * **The refusal-clearing path is not entered.** ``note_capacity_report``
      is what could *clear* a recorded refusal, and a healthy poll has no
      business calling it.
    * **The probe is not called twice.** One admin round trip per poll is
      the budget; two would double the worst case on the ``/status``
      thread, which is the one request that has to answer while things are
      broken.
    """
    storage_probe(None)
    calls: list[dict] = []
    reports: list[dict] = []

    def _record(**kwargs):
        calls.append(kwargs)
        return 2 * 1024**3

    monkeypatch.setattr(artifact_storage_admin, "report_free_bytes", _record)
    monkeypatch.setattr(
        capacity, "note_capacity_report", lambda **kw: reports.append(kw)
    )
    with capacity_db() as session:
        before = session.query(ArtifactStorageCapacityEvent).count()

    body = client.get("/api/v1/status").json()

    assert body["components"]["artifact_storage"]["healthy"] is True
    assert len(calls) == 1, (
        f"a healthy poll made {len(calls)} admin round trips; the budget is one"
    )
    assert reports == [], "a healthy poll wrote a capacity report"
    with capacity_db() as session:
        assert session.query(ArtifactStorageCapacityEvent).count() == before, (
            "a healthy poll appended to the capacity log, turning an incident "
            "record into a metric series"
        )


def test_a_store_with_no_capacity_opinion_leaves_the_refusal_standing(
    client, storage_probe, capacity_db, monkeypatch
) -> None:
    """A non-MinIO store answers nothing, and nothing is not "there is room".

    ``report_free_bytes`` returns ``None`` for AWS S3, a 403, a 404, a
    timeout, or an unfamiliar body. Every one of those must leave the
    write path's own evidence exactly as it was — a probe that could clear
    a refusal by failing would be worse than no probe.
    """
    storage_probe(None)
    _observe_a_full_store(capacity_db)
    monkeypatch.setattr(
        artifact_storage_admin, "report_free_bytes", lambda **_kwargs: None
    )

    block = client.get("/api/v1/status").json()["components"]["artifact_storage"]
    assert block["storage_full"] is True
    assert block["healthy"] is False


def test_a_capacity_report_cannot_clear_a_bucket_quota_refusal_on_status(
    client, storage_probe, capacity_db, monkeypatch
) -> None:
    """Measured: 418 MiB free while a 2 MiB write was refused for quota.

    A quota refusal is invisible to a free-space number, so ``/status``
    must not consult one to clear it — and must not even ask, since the
    answer could not be used.
    """
    storage_probe(None)
    capacity.record_refusal(
        s3_code="XMinioAdminBucketQuotaExceeded",
        attempted_bytes=2_097_152,
        detail="Bucket quota exceeded",
        session_factory=capacity_db,
    )
    calls: list[dict] = []

    def _record(**kwargs):
        calls.append(kwargs)
        return 437_858_304

    monkeypatch.setattr(artifact_storage_admin, "report_free_bytes", _record)

    block = client.get("/api/v1/status").json()["components"]["artifact_storage"]
    assert block["storage_full"] is True, (
        "418 MiB of free disk cleared a bucket-quota refusal on /status"
    )
    assert calls == [], "the probe was asked a question its answer cannot settle"


# ---------------------------------------------------------------------------
# Approaching full: a warning, which is deliberately not a degradation
# ---------------------------------------------------------------------------
#
# The load-bearing distinction in this section. ``degraded`` is what BOTH
# alerters page on -- the Pi-side checker at Priority: high, the GitHub probe
# at Priority: urgent every fifteen minutes until it clears. A store that is
# 90 % full is still accepting every upload, so paging on it would train an
# operator to swipe the channel away, taking the real outages with it. So the
# warning goes in its own array, the component stays green, and ``degraded``
# stays empty. Every test below asserts all three, and
# ``test_a_warning_is_quiet_where_a_refusal_is_loud`` proves the empty-degraded
# assertion is not vacuous by making it non-empty in the same test body.

CEILING = artifact_storage.MAX_ARTIFACT_BYTES


def _artifact_warnings(body: dict) -> list[dict]:
    return [w for w in body["warnings"] if w["component"] == "artifact_storage"]


def test_ample_headroom_raises_no_warning(client, storage_probe, monkeypatch) -> None:
    """The common case, and the one a noisy implementation would ruin."""
    storage_probe(None)
    monkeypatch.setattr(
        artifact_storage_admin, "report_free_bytes", lambda **_k: 50 * 1024**3
    )
    monkeypatch.setattr(
        artifact_storage_admin, "report_bucket_quota_bytes", lambda **_k: 200 * 1024**3
    )

    body = client.get("/api/v1/status").json()
    assert body["warnings"] == []
    assert body["degraded"] == []
    assert body["status"] == "ok"
    assert body["components"]["artifact_storage"]["warnings"] == []


def test_headroom_below_the_artifact_ceiling_warns_without_degrading(
    client, storage_probe, monkeypatch
) -> None:
    """The whole feature, in one assertion block.

    Free space has fallen below the largest artifact TCKDB will accept, so
    the next full-size upload may be refused -- but nothing has been
    refused yet and every upload still works. That is a warning, not an
    outage: ``healthy`` stays true, ``status`` stays ``ok``, ``degraded``
    stays empty, and the notice lands in ``warnings``.
    """
    storage_probe(None)
    monkeypatch.setattr(
        artifact_storage_admin, "report_free_bytes", lambda **_k: CEILING // 2
    )

    body = client.get("/api/v1/status").json()

    assert body["status"] == "ok"
    assert body["degraded"] == [], (
        "an approaching-full store was routed down the paging channel"
    )
    assert body["components"]["artifact_storage"]["healthy"] is True

    warnings = _artifact_warnings(body)
    assert len(warnings) == 1, body["warnings"]
    warning = warnings[0]
    assert warning["code"] == "artifact_storage_headroom_low"
    assert warning["headroom_bytes"] == CEILING // 2
    assert warning["max_artifact_bytes"] == CEILING
    assert warning["source"] == "free_space"
    assert warning["measured_at"]
    # The number, not a ratio. A percentage would have been the easy thing
    # to report and the wrong thing: "50 % full" reads as safe.
    assert f"{CEILING // 2:,}" in warning["summary"]
    assert "%" not in warning["summary"]


def test_a_warning_is_quiet_where_a_refusal_is_loud(
    client, storage_probe, capacity_db, monkeypatch
) -> None:
    """Both halves in one test, so neither assertion can be vacuous.

    ``degraded == []`` proves nothing on its own -- it would pass against
    an endpoint that never populates ``degraded`` at all. So the same test
    body then records a real refusal and shows the field going non-empty
    while ``warnings`` stays empty. The two signals are on separate wires,
    and this is what demonstrates it.
    """
    storage_probe(None)
    # Below the 4 MiB refusal recorded further down, deliberately: a
    # free-space number at or above the refused size *answers* the refusal
    # (that is what ``report_free_bytes`` is for), and the store would go
    # green again mid-test. Being genuinely short of room is the state this
    # test is about.
    monkeypatch.setattr(
        artifact_storage_admin, "report_free_bytes", lambda **_k: 1_000_000
    )

    warned = client.get("/api/v1/status").json()
    assert warned["degraded"] == []
    assert len(_artifact_warnings(warned)) == 1

    # Now the store actually refuses something. Same endpoint, same poll.
    _observe_a_full_store(capacity_db, attempted_bytes=4_194_304)
    refused = client.get("/api/v1/status").json()

    assert refused["degraded"] == ["artifact_storage"], (
        "the degraded field cannot go non-empty, so asserting it is empty "
        "above proves nothing"
    )
    assert refused["status"] == "degraded"
    assert refused["components"]["artifact_storage"]["healthy"] is False
    assert refused["warnings"] == [], (
        "a store that has already refused a write does not also need to be "
        "told it is getting full; the probe is skipped and says nothing"
    )


def test_a_quota_that_is_nearly_used_up_warns_and_names_the_quota(
    client, storage_probe, monkeypatch
) -> None:
    """The arm free space cannot see.

    Measured: 418 MiB of free disk while a 2 MiB write was refused for
    quota. An operator handed only "free space" here would go looking at
    the disk. So the warning names its source, and the remedy in the
    summary is "raise the quota", not "free disk".
    """
    storage_probe(None)
    monkeypatch.setattr(
        artifact_storage_admin, "report_free_bytes", lambda **_k: 400 * 1024**2
    )
    monkeypatch.setattr(
        artifact_storage_admin,
        "report_bucket_quota_bytes",
        lambda **_k: 20 * 1024**2,
    )

    body = client.get("/api/v1/status").json()
    warning = _artifact_warnings(body)[0]

    assert body["degraded"] == []
    assert warning["source"] == "bucket_quota"
    assert warning["quota_bytes"] == 20 * 1024**2
    assert warning["ledger_bytes"] is not None
    assert warning["quota_age_seconds"] == 0.0, (
        "a freshly fetched quota must report zero age, not None"
    )
    assert "quota" in warning["summary"]


def test_an_unreachable_quota_api_is_no_opinion_and_no_warning(
    client, storage_probe, monkeypatch
) -> None:
    """A store that will not answer is not a store that is full.

    AWS S3, a proxy that does not route the admin API, a credential
    without ``admin:GetBucketQuota``, a timeout. Every one of them returns
    ``None``, and ``None`` must never become a warning -- a monitor that
    cries wolf whenever it cannot see is a monitor that gets turned off.
    """
    storage_probe(None)

    def _unreachable(**_kwargs):
        return None

    monkeypatch.setattr(
        artifact_storage_admin, "report_bucket_quota_bytes", _unreachable
    )
    monkeypatch.setattr(artifact_storage_admin, "report_free_bytes", _unreachable)

    body = client.get("/api/v1/status").json()
    assert body["warnings"] == []
    assert body["degraded"] == []
    assert body["status"] == "ok"
    assert body["components"]["artifact_storage"]["healthy"] is True


def test_no_quota_configured_leaves_the_free_space_arm_to_answer(
    client, storage_probe, monkeypatch
) -> None:
    """The default deployment: MinIO with no bucket quota set.

    ``quota: 0`` means "not configured", so that arm has no opinion --
    which must not silence the other one, or the feature would do nothing
    on the deployment it was written for.
    """
    storage_probe(None)
    monkeypatch.setattr(
        artifact_storage_admin, "report_bucket_quota_bytes", lambda **_k: None
    )
    monkeypatch.setattr(
        artifact_storage_admin, "report_free_bytes", lambda **_k: 1024
    )

    body = client.get("/api/v1/status").json()
    warning = _artifact_warnings(body)[0]
    assert warning["source"] == "free_space"
    assert warning["quota_bytes"] is None
    assert warning["quota_age_seconds"] is None
    assert body["degraded"] == []


def test_an_unreachable_store_is_not_also_told_it_is_getting_full(
    client, storage_probe, monkeypatch
) -> None:
    """One fault, one message.

    The probe would cost up to two admin round trips against an endpoint
    that has already failed to answer -- added to the one poll that most
    needs to answer quickly -- and "you are running out of room" is not
    what an operator staring at an unreachable object store needs to read.
    """
    storage_probe(EndpointConnectionError(endpoint_url=PROBE_ENDPOINT))
    asked: list[str] = []
    monkeypatch.setattr(
        artifact_storage_admin,
        "report_free_bytes",
        lambda **_k: asked.append("free") or 1,
    )
    monkeypatch.setattr(
        artifact_storage_admin,
        "report_bucket_quota_bytes",
        lambda **_k: asked.append("quota") or 1,
    )

    body = client.get("/api/v1/status").json()
    assert body["degraded"] == ["artifact_storage"]
    assert body["warnings"] == []
    assert asked == [], "the headroom probe ran against a store that is not there"


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
