"""Worker-layer tests for the async upload job queue.

These tests exercise ``app.workers.upload_worker`` directly — claim
ordering, retry behavior, terminal failure, and dispatch routing — without
going through the API layer and without exercising any real workflow.
Workflow entry points are monkeypatched so each test stays focused on
worker state transitions and dispatch routing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

import app.workers.upload_worker as upload_worker
from app.db.models.common import UploadJobKind, UploadJobStatus
from app.db.models.upload_job import UploadJob


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def worker_db(db_engine, monkeypatch) -> Iterator[Session]:
    """Give tests a session bound to the real test engine and point the
    worker's ``SessionLocal`` at it so ``_process_one_cycle`` commits land
    in the same database the test can read back.

    Tests insert via ``with session.begin():`` blocks (committing) and any
    ``upload_job`` rows are removed on teardown so tests stay independent.
    """
    TestSessionLocal = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(upload_worker, "SessionLocal", TestSessionLocal)

    session = Session(bind=db_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        with Session(db_engine) as cleanup:
            with cleanup.begin():
                cleanup.execute(text("DELETE FROM upload_job"))


def _insert_job(
    session: Session,
    *,
    kind: UploadJobKind = UploadJobKind.thermo,
    status: UploadJobStatus = UploadJobStatus.queued,
    created_at: datetime | None = None,
    attempts: int = 0,
    max_attempts: int = 3,
    payload: dict | None = None,
) -> UploadJob:
    job = UploadJob(
        kind=kind,
        status=status,
        payload=payload if payload is not None else {},
        attempts=attempts,
        max_attempts=max_attempts,
    )
    if created_at is not None:
        job.created_at = created_at
    session.add(job)
    session.flush()
    return job


# ---------------------------------------------------------------------------
# _claim_one_job
# ---------------------------------------------------------------------------


def test_claim_one_job_returns_oldest_queued(worker_db):
    base = datetime(2026, 4, 20, 12, 0, 0)
    with worker_db.begin():
        oldest = _insert_job(worker_db, created_at=base - timedelta(hours=2))
        middle = _insert_job(worker_db, created_at=base - timedelta(hours=1))
        newest = _insert_job(worker_db, created_at=base)
        oldest_id, middle_id, newest_id = oldest.id, middle.id, newest.id

    with worker_db.begin():
        claimed = upload_worker._claim_one_job(worker_db)
        assert claimed is not None
        assert claimed.id == oldest_id
        assert claimed.status == UploadJobStatus.processing
        assert claimed.started_at is not None
        assert claimed.attempts == 1

    with worker_db.begin():
        worker_db.expire_all()
        assert worker_db.get(UploadJob, middle_id).status == UploadJobStatus.queued
        assert worker_db.get(UploadJob, newest_id).status == UploadJobStatus.queued


def test_claim_one_job_skips_non_queued_jobs(worker_db):
    base = datetime(2026, 4, 20, 12, 0, 0)
    with worker_db.begin():
        # Older but already processing / complete / failed — must be skipped.
        _insert_job(
            worker_db,
            status=UploadJobStatus.processing,
            created_at=base - timedelta(hours=3),
        )
        _insert_job(
            worker_db,
            status=UploadJobStatus.complete,
            created_at=base - timedelta(hours=2),
        )
        _insert_job(
            worker_db,
            status=UploadJobStatus.failed,
            created_at=base - timedelta(hours=1),
            attempts=3,
            max_attempts=3,
        )
        queued = _insert_job(worker_db, created_at=base)
        queued_id = queued.id

    with worker_db.begin():
        claimed = upload_worker._claim_one_job(worker_db)
        assert claimed is not None
        assert claimed.id == queued_id
        assert claimed.status == UploadJobStatus.processing


def test_claim_one_job_skips_attempts_exhausted_queued(worker_db):
    """A queued row with ``attempts >= max_attempts`` is not claimable."""
    base = datetime(2026, 4, 20, 12, 0, 0)
    with worker_db.begin():
        _insert_job(
            worker_db,
            created_at=base - timedelta(hours=1),
            attempts=3,
            max_attempts=3,
        )
        fresh = _insert_job(worker_db, created_at=base)
        fresh_id = fresh.id

    with worker_db.begin():
        claimed = upload_worker._claim_one_job(worker_db)
        assert claimed is not None
        assert claimed.id == fresh_id


def test_claim_one_job_returns_none_when_queue_empty(worker_db):
    with worker_db.begin():
        assert upload_worker._claim_one_job(worker_db) is None


# ---------------------------------------------------------------------------
# run_one_job — happy path
# ---------------------------------------------------------------------------


def test_process_one_cycle_marks_job_complete_on_success(worker_db, monkeypatch):
    expected_result = {"type": "thermo", "id": 1234, "species_entry_id": 42}

    def stub_handler(session, job):
        return expected_result

    monkeypatch.setitem(upload_worker._DISPATCH, UploadJobKind.thermo, stub_handler)

    with worker_db.begin():
        job = _insert_job(worker_db, kind=UploadJobKind.thermo, payload={"x": 1})
        job_id = job.id

    did_work = upload_worker._process_one_cycle()
    assert did_work is True

    with worker_db.begin():
        worker_db.expire_all()
        persisted = worker_db.get(UploadJob, job_id)
        assert persisted.status == UploadJobStatus.complete
        assert persisted.completed_at is not None
        assert persisted.result == expected_result
        assert persisted.error is None
        assert persisted.attempts == 1


# ---------------------------------------------------------------------------
# Retry with attempts remaining
# ---------------------------------------------------------------------------


def test_process_one_cycle_requeues_on_failure_when_attempts_remain(
    worker_db, monkeypatch,
):
    def failing_handler(session, job):
        raise ValueError("boom")

    monkeypatch.setitem(upload_worker._DISPATCH, UploadJobKind.thermo, failing_handler)

    with worker_db.begin():
        job = _insert_job(
            worker_db,
            kind=UploadJobKind.thermo,
            attempts=0,
            max_attempts=3,
        )
        job_id = job.id

    did_work = upload_worker._process_one_cycle()
    assert did_work is True

    with worker_db.begin():
        worker_db.expire_all()
        persisted = worker_db.get(UploadJob, job_id)
        assert persisted.status == UploadJobStatus.queued
        assert persisted.status != UploadJobStatus.failed
        assert persisted.attempts == 1
        assert persisted.completed_at is None
        assert persisted.error is not None
        assert "ValueError" in persisted.error
        assert "boom" in persisted.error


# ---------------------------------------------------------------------------
# Terminal failure
# ---------------------------------------------------------------------------


def test_process_one_cycle_marks_failed_when_attempts_exhausted(
    worker_db, monkeypatch,
):
    def failing_handler(session, job):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(upload_worker._DISPATCH, UploadJobKind.thermo, failing_handler)

    # Seed at max_attempts - 1 so the claim increment lands exactly at
    # ``max_attempts``, triggering the terminal-failed branch.
    with worker_db.begin():
        job = _insert_job(
            worker_db,
            kind=UploadJobKind.thermo,
            attempts=2,
            max_attempts=3,
        )
        job_id = job.id

    did_work = upload_worker._process_one_cycle()
    assert did_work is True

    with worker_db.begin():
        worker_db.expire_all()
        persisted = worker_db.get(UploadJob, job_id)
        assert persisted.status == UploadJobStatus.failed
        assert persisted.attempts == 3
        assert persisted.error is not None
        assert "RuntimeError" in persisted.error
        assert "kaboom" in persisted.error
        assert persisted.completed_at is not None

    # A follow-up cycle must not re-queue or re-claim the terminally failed job.
    assert upload_worker._process_one_cycle() is False
    with worker_db.begin():
        worker_db.expire_all()
        assert worker_db.get(UploadJob, job_id).status == UploadJobStatus.failed


# ---------------------------------------------------------------------------
# Dispatch coverage across all UploadJobKind values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(UploadJobKind))
def test_dispatch_routes_each_kind_to_its_handler(worker_db, monkeypatch, kind):
    """Every ``UploadJobKind`` has a registered handler, the right one is
    invoked for the claimed job, the payload is passed through, and the
    handler's return value propagates to ``job.result`` on completion.
    """
    calls: list[tuple[UploadJobKind, dict]] = []

    def stub_handler(session, job):
        calls.append((job.kind, dict(job.payload)))
        return {"dispatched_kind": job.kind.value, "echo": job.payload}

    # Replace every handler: if dispatch routes to the wrong kind, we'll
    # still land in a stub but ``calls[0][0]`` won't match the expected kind.
    stub_dispatch = {k: stub_handler for k in UploadJobKind}
    monkeypatch.setattr(upload_worker, "_DISPATCH", stub_dispatch)

    payload = {"kind_marker": kind.value, "n": 7}
    with worker_db.begin():
        job = _insert_job(worker_db, kind=kind, payload=payload)
        job_id = job.id

    did_work = upload_worker._process_one_cycle()
    assert did_work is True

    assert len(calls) == 1
    called_kind, called_payload = calls[0]
    assert called_kind == kind
    assert called_payload == payload

    with worker_db.begin():
        worker_db.expire_all()
        persisted = worker_db.get(UploadJob, job_id)
        assert persisted.status == UploadJobStatus.complete
        assert persisted.result == {
            "dispatched_kind": kind.value,
            "echo": payload,
        }


def test_dispatch_registry_has_entry_for_every_kind():
    """Guard against silently dropping a handler when a new ``UploadJobKind``
    is added to the enum."""
    assert set(upload_worker._DISPATCH.keys()) == set(UploadJobKind)


# ---------------------------------------------------------------------------
# No-op when queue is empty
# ---------------------------------------------------------------------------


def test_process_one_cycle_returns_false_when_queue_empty(worker_db):
    assert upload_worker._process_one_cycle() is False


# ---------------------------------------------------------------------------
# Transport-specific parity coverage
# ---------------------------------------------------------------------------
#
# These tests guarantee transport stays part of async upload coverage: if the
# enum entry, dispatch entry, or workflow handler regress, these tests fail
# loudly rather than silently skipping transport from the worker.


def _transport_job_payload() -> dict:
    """Minimal valid transport upload payload for worker-dispatch tests."""
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "sigma_angstrom": 2.05,
        "epsilon_over_k_k": 145.0,
        "dipole_debye": 0.0,
        "polarizability_angstrom3": 0.667,
        "rotational_relaxation": 0.0,
        "note": "H atom transport (worker test)",
    }


def test_transport_has_registered_dispatch_handler():
    """Transport must be a first-class upload kind, with a dedicated handler
    wired into the worker's dispatch table — not silently dropped.
    """
    assert UploadJobKind.transport in upload_worker._DISPATCH
    assert upload_worker._DISPATCH[UploadJobKind.transport] is upload_worker._run_transport


def test_worker_dispatches_transport_job_to_transport_handler(
    worker_db, monkeypatch,
):
    """A queued transport job is claimed and routed to the transport handler,
    not to any other kind's handler.
    """
    calls: list[UploadJobKind] = []

    def stub_transport(session, job):
        calls.append(job.kind)
        return {"type": "transport", "id": 999, "species_entry_id": 1}

    def wrong_handler(session, job):  # pragma: no cover — must not be called
        raise AssertionError(
            f"transport job was misrouted to handler for {job.kind!r}"
        )

    stub_dispatch = {k: wrong_handler for k in UploadJobKind}
    stub_dispatch[UploadJobKind.transport] = stub_transport
    monkeypatch.setattr(upload_worker, "_DISPATCH", stub_dispatch)

    with worker_db.begin():
        job = _insert_job(
            worker_db,
            kind=UploadJobKind.transport,
            payload={"marker": "transport"},
        )
        job_id = job.id

    did_work = upload_worker._process_one_cycle()
    assert did_work is True
    assert calls == [UploadJobKind.transport]

    with worker_db.begin():
        worker_db.expire_all()
        persisted = worker_db.get(UploadJob, job_id)
        assert persisted.status == UploadJobStatus.complete
        assert persisted.result["type"] == "transport"


def test_run_transport_handler_persists_transport_via_canonical_workflow(
    db_session, _api_test_user,
):
    """The ``_run_transport`` handler goes through the canonical transport
    workflow (``persist_transport_upload``), persists a Transport row scoped
    to the resolved species entry, and returns the standard result envelope
    — matching the sync upload path.

    This runs inside the API client's per-test transactional session so the
    resolved species/species_entry/transport rows are rolled back at teardown.
    """
    from app.db.models.transport import Transport

    job = UploadJob(
        kind=UploadJobKind.transport,
        status=UploadJobStatus.processing,
        payload=_transport_job_payload(),
        attempts=1,
        max_attempts=3,
        created_by=_api_test_user,
    )

    result = upload_worker._run_transport(db_session, job)

    assert result["type"] == "transport"
    assert "id" in result
    assert "species_entry_id" in result

    transport = db_session.get(Transport, result["id"])
    assert transport is not None
    assert transport.species_entry_id == result["species_entry_id"]


def test_transport_job_is_requeued_on_transient_failure(worker_db, monkeypatch):
    """A failing transport handler below max_attempts must leave the job
    back in ``queued`` with attempts incremented, not prematurely failed.
    """
    def flaky_handler(session, job):
        raise ValueError("transient transport failure")

    monkeypatch.setitem(
        upload_worker._DISPATCH, UploadJobKind.transport, flaky_handler,
    )

    with worker_db.begin():
        job = _insert_job(
            worker_db,
            kind=UploadJobKind.transport,
            attempts=0,
            max_attempts=3,
        )
        job_id = job.id

    assert upload_worker._process_one_cycle() is True

    with worker_db.begin():
        worker_db.expire_all()
        persisted = worker_db.get(UploadJob, job_id)
        assert persisted.status == UploadJobStatus.queued
        assert persisted.attempts == 1
        assert persisted.completed_at is None
        assert "ValueError" in persisted.error
        assert "transient transport failure" in persisted.error


def test_transport_job_is_marked_failed_when_attempts_exhausted(
    worker_db, monkeypatch,
):
    """After ``max_attempts`` failures a transport job must terminate in
    ``failed`` with the error populated and no successful result.
    """
    def always_failing(session, job):
        raise RuntimeError("permanent transport failure")

    monkeypatch.setitem(
        upload_worker._DISPATCH, UploadJobKind.transport, always_failing,
    )

    with worker_db.begin():
        job = _insert_job(
            worker_db,
            kind=UploadJobKind.transport,
            attempts=2,
            max_attempts=3,
        )
        job_id = job.id

    assert upload_worker._process_one_cycle() is True

    with worker_db.begin():
        worker_db.expire_all()
        persisted = worker_db.get(UploadJob, job_id)
        assert persisted.status == UploadJobStatus.failed
        assert persisted.attempts == 3
        assert persisted.result is None
        assert persisted.completed_at is not None
        assert "RuntimeError" in persisted.error
        assert "permanent transport failure" in persisted.error
