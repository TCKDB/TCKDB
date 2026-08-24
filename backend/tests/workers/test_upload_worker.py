"""Worker-layer tests for the async upload job queue.

These tests exercise worker claims, recovery, fencing, dispatch, and both
stubbed and real workflow persistence against the PostgreSQL-backed queue.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import func, select, text
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
                cleanup.execute(
                    text(
                        "DELETE FROM upload_job WHERE NOT EXISTS (SELECT 1 FROM submission WHERE submission.upload_job_id = upload_job.id)"
                    )
                )


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
        upload_worker._release_execution_lock(worker_db, str(claimed.id))

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
        upload_worker._release_execution_lock(worker_db, str(claimed.id))


def test_claim_one_job_recovers_expired_lease_exactly_once(worker_db, monkeypatch):
    """A worker killed immediately after claim leaves recoverable, not duplicate work."""
    now = datetime(2026, 4, 20, 12, 0, 0)
    monkeypatch.setattr(upload_worker, "_utcnow", lambda: now)
    calls: list[str] = []

    def handler(session, job, review_policy=None):
        calls.append(job.id)
        return {"id": 91}

    monkeypatch.setitem(upload_worker._DISPATCH, UploadJobKind.thermo, handler)
    with worker_db.begin():
        job = _insert_job(worker_db, kind=UploadJobKind.thermo)
        job_id = job.id
        claimed = upload_worker._claim_one_job(worker_db)
        assert claimed is not None
        assert claimed.id == job_id
        # Simulate process death after the claim transaction committed: its
        # session-scoped lock vanishes with the dead connection.
        upload_worker._release_execution_lock(worker_db, str(claimed.id))

    monkeypatch.setattr(upload_worker, "_utcnow", lambda: now + upload_worker._LEASE_DURATION + timedelta(seconds=1))
    assert upload_worker._process_one_cycle() is True
    assert calls == [job_id]
    assert upload_worker._process_one_cycle() is False
    assert calls == [job_id]

    with worker_db.begin():
        worker_db.expire_all()
        persisted = worker_db.get(UploadJob, job_id)
        assert persisted.status == UploadJobStatus.complete
        assert persisted.attempts == 2
        assert persisted.lease_expires_at is None
        assert persisted.heartbeat_at is not None


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
        upload_worker._release_execution_lock(worker_db, str(claimed.id))


def test_claim_one_job_returns_none_when_queue_empty(worker_db):
    with worker_db.begin():
        assert upload_worker._claim_one_job(worker_db) is None


def test_claim_skips_fenced_job_without_mutation_and_claims_next(worker_db, db_engine):
    """A live executor fence must be checked before claim state changes."""
    base = datetime(2026, 4, 20, 12, 0, 0)
    with worker_db.begin():
        fenced = _insert_job(worker_db, created_at=base - timedelta(minutes=1))
        eligible = _insert_job(worker_db, created_at=base)
        fenced_id, eligible_id = fenced.id, eligible.id
        before = (fenced.status, fenced.attempts, fenced.lease_expires_at, fenced.heartbeat_at)

    with db_engine.connect() as owner_connection:
        owner = Session(bind=owner_connection, expire_on_commit=False)
        try:
            with owner.begin():
                assert upload_worker._try_execution_lock(owner, str(fenced_id))

            with worker_db.begin():
                claimed = upload_worker._claim_one_job(worker_db)
                assert claimed is not None
                assert claimed.id == eligible_id
                # Direct claim tests must release the returned session lock.
                upload_worker._release_execution_lock(worker_db, str(claimed.id))

            with worker_db.begin():
                worker_db.expire_all()
                unchanged = worker_db.get(UploadJob, fenced_id)
                assert (
                    unchanged.status,
                    unchanged.attempts,
                    unchanged.lease_expires_at,
                    unchanged.heartbeat_at,
                ) == before
        finally:
            with owner.begin():
                upload_worker._release_execution_lock(owner, str(fenced_id))
            owner.close()


def test_reaper_skips_fenced_final_attempt_then_terminalizes_after_release(
    worker_db,
    db_engine,
    monkeypatch,
    _api_test_user,
):
    """The expiry reaper shares the executor fence and cannot race its submission."""
    from app.db.models.common import SubmissionStatus
    from app.db.models.submission import Submission
    from app.services.upload_submission import open_job_submission

    now = datetime(2026, 4, 20, 12, 0, 0)
    monkeypatch.setattr(upload_worker, "_utcnow", lambda: now)
    with worker_db.begin():
        job = _insert_job(
            worker_db,
            status=UploadJobStatus.processing,
            attempts=3,
            max_attempts=3,
        )
        job.created_by = _api_test_user
        job.lease_expires_at = now - timedelta(seconds=1)
        job.heartbeat_at = now - timedelta(minutes=1)
        submission = open_job_submission(
            worker_db,
            created_by=_api_test_user,
            job_kind=job.kind,
            upload_job_id=str(job.id),
        )
        job_id, submission_id = job.id, submission.id

    with db_engine.connect() as owner_connection:
        owner = Session(bind=owner_connection, expire_on_commit=False)
        try:
            with owner.begin():
                assert upload_worker._try_execution_lock(owner, str(job_id))

            with worker_db.begin():
                assert upload_worker._fail_expired_exhausted_jobs(worker_db) == 0

            with worker_db.begin():
                worker_db.expire_all()
                persisted = worker_db.get(UploadJob, job_id)
                assert persisted.status is UploadJobStatus.processing
                assert persisted.lease_expires_at == now - timedelta(seconds=1)
                assert worker_db.get(Submission, submission_id).status is SubmissionStatus.pending

            with owner.begin():
                upload_worker._release_execution_lock(owner, str(job_id))

            with worker_db.begin():
                assert upload_worker._fail_expired_exhausted_jobs(worker_db) == 1

            with worker_db.begin():
                worker_db.expire_all()
                persisted = worker_db.get(UploadJob, job_id)
                assert persisted.status is UploadJobStatus.failed
                assert persisted.lease_expires_at is None
                assert worker_db.get(Submission, submission_id).status is SubmissionStatus.failed
        finally:
            owner.close()
            with worker_db.begin():
                worker_db.execute(
                    text("DELETE FROM submission_audit_event WHERE submission_id = :sid"),
                    {"sid": submission_id},
                )
                worker_db.execute(
                    text("DELETE FROM submission_record_link WHERE submission_id = :sid"),
                    {"sid": submission_id},
                )
                worker_db.execute(text("DELETE FROM submission WHERE id = :sid"), {"sid": submission_id})


# ---------------------------------------------------------------------------
# run_one_job — happy path
# ---------------------------------------------------------------------------


def test_process_one_cycle_marks_job_complete_on_success(worker_db, monkeypatch):
    expected_result = {"type": "thermo", "id": 1234, "species_entry_id": 42}

    def stub_handler(session, job, review_policy=None):
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


def test_processing_job_receives_periodic_heartbeat(worker_db, monkeypatch):
    """A slow handler renews its lease from a second worker-session."""
    original = upload_worker._heartbeat_job
    heartbeats: list[str] = []
    monkeypatch.setattr(upload_worker, "_HEARTBEAT_INTERVAL", 0.01)

    def recording_heartbeat(session, job):
        heartbeats.append(job.id)
        original(session, job)

    def slow_handler(session, job, review_policy=None):
        time.sleep(0.04)
        return {"id": 8}

    monkeypatch.setattr(upload_worker, "_heartbeat_job", recording_heartbeat)
    monkeypatch.setitem(upload_worker._DISPATCH, UploadJobKind.thermo, slow_handler)
    with worker_db.begin():
        _insert_job(worker_db, kind=UploadJobKind.thermo)

    assert upload_worker._process_one_cycle() is True
    assert heartbeats


def test_execution_advisory_lock_fences_a_second_worker(worker_db):
    """An expired lease cannot make two workers execute one scientific write."""
    with worker_db.begin():
        job = _insert_job(worker_db, kind=UploadJobKind.thermo)
        job_id = job.id

    assert upload_worker._try_execution_lock(worker_db, job_id) is True
    contender = Session(bind=worker_db.get_bind(), expire_on_commit=False)
    try:
        assert upload_worker._try_execution_lock(contender, job_id) is False
    finally:
        upload_worker._release_execution_lock(worker_db, job_id)
        contender.close()


def test_reaper_terminalizes_exhausted_queued_job(worker_db):
    with worker_db.begin():
        job = _insert_job(worker_db, attempts=3, max_attempts=3)
        job_id = job.id

    assert upload_worker._process_one_cycle() is True
    with worker_db.begin():
        worker_db.expire_all()
        persisted = worker_db.get(UploadJob, job_id)
        assert persisted.status == UploadJobStatus.failed
        assert persisted.completed_at is not None


# ---------------------------------------------------------------------------
# Retry with attempts remaining
# ---------------------------------------------------------------------------


def test_process_one_cycle_requeues_on_failure_when_attempts_remain(
    worker_db,
    monkeypatch,
):
    def failing_handler(session, job, review_policy=None):
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
    worker_db,
    monkeypatch,
):
    def failing_handler(session, job, review_policy=None):
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

    def stub_handler(session, job, review_policy=None):
        calls.append((job.kind, dict(job.payload)))
        return {"dispatched_kind": job.kind.value, "echo": job.payload}

    # Replace every handler: if dispatch routes to the wrong kind, we'll
    # still land in a stub but ``calls[0][0]`` won't match the expected kind.
    stub_dispatch = dict.fromkeys(UploadJobKind, stub_handler)
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
    worker_db,
    monkeypatch,
):
    """A queued transport job is claimed and routed to the transport handler,
    not to any other kind's handler.
    """
    calls: list[UploadJobKind] = []

    def stub_transport(session, job, review_policy=None):
        calls.append(job.kind)
        return {"type": "transport", "id": 999, "species_entry_id": 1}

    def wrong_handler(session, job, review_policy=None):  # pragma: no cover — must not be called
        raise AssertionError(f"transport job was misrouted to handler for {job.kind!r}")

    stub_dispatch = dict.fromkeys(UploadJobKind, wrong_handler)
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
    db_session,
    _api_test_user,
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

    result = upload_worker._run_transport(db_session, job, upload_worker.ReviewPolicy())

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

    def flaky_handler(session, job, review_policy=None):
        raise ValueError("transient transport failure")

    monkeypatch.setitem(
        upload_worker._DISPATCH,
        UploadJobKind.transport,
        flaky_handler,
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
    worker_db,
    monkeypatch,
):
    """After ``max_attempts`` failures a transport job must terminate in
    ``failed`` with the error populated and no successful result.
    """

    def always_failing(session, job, review_policy=None):
        raise RuntimeError("permanent transport failure")

    monkeypatch.setitem(
        upload_worker._DISPATCH,
        UploadJobKind.transport,
        always_failing,
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


# ---------------------------------------------------------------------------
# Submission/audit/review wiring (ingestion-audit model)
# ---------------------------------------------------------------------------


def _thermo_job_payload() -> dict:
    """Minimal valid thermo upload payload for worker-submission tests."""
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "h298_kj_mol": 217.998,
    }


def test_run_one_job_links_records_and_initializes_not_reviewed(
    db_session,
    _api_test_user,
):
    """A worker success persists records awaiting review, links them to the
    job's submission, and appends an ``ingestion_succeeded`` audit event.

    ``not_reviewed``, not ``under_review``: the async path shares
    ``review_policy_for_submission`` with the synchronous routes, and a
    background worker finishing a job is even further from a human having
    read the result than a foreground upload is.

    Exercised through ``run_one_job`` on the per-test transactional session
    so everything rolls back at teardown (no committed pollution).
    """
    from sqlalchemy import select

    from app.db.models.common import (
        RecordReviewStatus,
        SubmissionAuditEventKind,
        SubmissionRecordType,
    )
    from app.db.models.record_review import RecordReview
    from app.db.models.submission import SubmissionAuditEvent, SubmissionRecordLink
    from app.services.upload_submission import open_job_submission

    job = UploadJob(
        kind=UploadJobKind.thermo,
        status=UploadJobStatus.processing,
        payload=_thermo_job_payload(),
        attempts=1,
        max_attempts=3,
        created_by=_api_test_user,
    )
    db_session.add(job)
    db_session.flush()
    submission = open_job_submission(
        db_session,
        created_by=_api_test_user,
        job_kind=UploadJobKind.thermo,
        upload_job_id=str(job.id),
    )
    db_session.flush()

    upload_worker.run_one_job(db_session, job)

    assert job.status == UploadJobStatus.complete
    assert job.result["submission_id"] == submission.id
    thermo_id = job.result["id"]

    # Records are linked to the submission.
    links = db_session.scalars(
        select(SubmissionRecordLink).where(SubmissionRecordLink.submission_id == submission.id)
    ).all()
    assert links, "worker success should create submission_record_link rows"

    # The thermo product awaits review and points at the submission.
    review = db_session.scalar(
        select(RecordReview).where(
            RecordReview.record_type == SubmissionRecordType.thermo,
            RecordReview.record_id == thermo_id,
        )
    )
    assert review is not None
    assert review.status is RecordReviewStatus.not_reviewed
    assert review.submission_id == submission.id
    assert review.reviewed_by is None
    assert review.reviewed_at is None

    # ingestion_succeeded audit event exists; submission stays pending.
    kinds = {
        e.event_kind
        for e in db_session.scalars(
            select(SubmissionAuditEvent).where(SubmissionAuditEvent.submission_id == submission.id)
        ).all()
    }
    assert SubmissionAuditEventKind.ingestion_succeeded in kinds


def test_abandoned_claim_recovers_real_thermo_workflow_exactly_once(worker_db, db_engine, _api_test_user):
    """A separately running worker claims, is terminated, then recovers once."""
    from app.db.models.species import Species, SpeciesEntry
    from app.db.models.thermo import Thermo
    from app.services.upload_submission import open_job_submission

    with worker_db.begin():
        before = worker_db.scalar(select(func.count()).select_from(Thermo)) or 0
        # High-water mark for the review bookkeeping this workflow will create.
        # The upload writes a `record_review` row per reviewable record — the
        # thermo *and* its species_entry — and only the thermo one used to be
        # cleaned up, so this test committed one review row into the shared
        # database on every run. Primary-key sequences are non-transactional and
        # only ever advance, so "id greater than this" is exactly the set of
        # review rows this test is responsible for, whatever record types the
        # workflow decides to review.
        before_review_id = (
            worker_db.scalar(text("SELECT max(id) FROM record_review")) or 0
        )
        existing_species_entry_id = worker_db.scalar(
            select(SpeciesEntry.id)
            .join(Species)
            .where(
                Species.smiles == "[H]",
                Species.charge == 0,
                Species.multiplicity == 2,
            )
        )
        job = _insert_job(worker_db, kind=UploadJobKind.thermo, payload=_thermo_job_payload())
        job.created_by = _api_test_user
        submission = open_job_submission(
            worker_db, created_by=_api_test_user, job_kind=job.kind, upload_job_id=str(job.id)
        )
        job_id = job.id
        submission_id = submission.id

    child_env = os.environ.copy()
    child_env.update(
        {
            "DB_NAME": db_engine.url.database,
            "DB_USER": "tckdb",
            "DB_PASSWORD": "tckdb",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
        }
    )
    child = None
    try:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from app.api.deps import SessionLocal; from app.workers.upload_worker import _claim_one_job; import time; s=SessionLocal(); s.begin(); j=_claim_one_job(s); s.commit(); time.sleep(60)",
            ],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=child_env,
        )
        for _ in range(200):
            with worker_db.begin():
                worker_db.expire_all()
                if worker_db.get(UploadJob, job_id).status is UploadJobStatus.processing:
                    break
            time.sleep(0.05)
        else:
            pytest.fail("child worker did not commit its claim")
        child.terminate()
        child.wait(timeout=10)
        with worker_db.begin():
            worker_db.get(UploadJob, job_id).lease_expires_at = datetime(2000, 1, 1)

        assert upload_worker._process_one_cycle() is True
        assert upload_worker._process_one_cycle() is False
        with worker_db.begin():
            worker_db.expire_all()
            persisted = worker_db.get(UploadJob, job_id)
            assert persisted.status is UploadJobStatus.complete
            assert (worker_db.scalar(select(func.count()).select_from(Thermo)) or 0) == before + 1
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()
        with Session(db_engine) as cleanup, cleanup.begin():
            # This test commits a real workflow to exercise crash recovery.
            # Its teardown is the sole test-only exception to append-only
            # audit history, confined to this transaction and these row ids.
            cleanup.execute(text("SET LOCAL session_replication_role = replica"))
            persisted = cleanup.get(UploadJob, job_id)
            thermo_id = persisted.result["id"] if persisted and persisted.result else None
            species_entry_id = persisted.result.get("species_entry_id") if persisted and persisted.result else None
            cleanup.execute(
                text(
                    "DELETE FROM record_review_event "
                    "WHERE record_review_id IN "
                    "(SELECT id FROM record_review WHERE id > :before_review_id)"
                ),
                {"before_review_id": before_review_id},
            )
            cleanup.execute(
                text("DELETE FROM record_review WHERE id > :before_review_id"),
                {"before_review_id": before_review_id},
            )
            if thermo_id is not None:
                cleanup.execute(
                    text("DELETE FROM thermo_source_calculation WHERE thermo_id = :thermo_id"), {"thermo_id": thermo_id}
                )
                cleanup.execute(text("DELETE FROM thermo_point WHERE thermo_id = :thermo_id"), {"thermo_id": thermo_id})
                cleanup.execute(
                    text("DELETE FROM thermo_nasa9_interval WHERE thermo_id = :thermo_id"), {"thermo_id": thermo_id}
                )
                cleanup.execute(text("DELETE FROM thermo_nasa WHERE thermo_id = :thermo_id"), {"thermo_id": thermo_id})
                cleanup.execute(
                    text("DELETE FROM thermo_wilhoit WHERE thermo_id = :thermo_id"), {"thermo_id": thermo_id}
                )
                cleanup.execute(text("DELETE FROM thermo WHERE id = :thermo_id"), {"thermo_id": thermo_id})
            cleanup.execute(
                text("DELETE FROM submission_audit_event WHERE submission_id = :submission_id"),
                {"submission_id": submission_id},
            )
            cleanup.execute(
                text("DELETE FROM submission_record_link WHERE submission_id = :submission_id"),
                {"submission_id": submission_id},
            )
            cleanup.execute(text("DELETE FROM submission WHERE id = :submission_id"), {"submission_id": submission_id})
            cleanup.execute(text("DELETE FROM upload_job WHERE id = :job_id"), {"job_id": job_id})
            if species_entry_id is not None and existing_species_entry_id is None:
                species_id = cleanup.scalar(
                    text("SELECT species_id FROM species_entry WHERE id = :species_entry_id"),
                    {"species_entry_id": species_entry_id},
                )
                cleanup.execute(
                    text("DELETE FROM species_entry WHERE id = :species_entry_id"),
                    {"species_entry_id": species_entry_id},
                )
                if species_id is not None:
                    cleanup.execute(
                        text(
                            "DELETE FROM species WHERE id = :species_id "
                            "AND NOT EXISTS (SELECT 1 FROM species_entry "
                            "WHERE species_entry.species_id = species.id)"
                        ),
                        {"species_id": species_id},
                    )


def test_terminal_worker_failure_records_durable_ingestion_failed(
    worker_db,
    monkeypatch,
    _api_test_user,
):
    """When a job exhausts retries, the worker durably marks its submission
    ``failed`` and appends an ``ingestion_failed`` audit event — with no
    partial scientific records (the persistence transaction rolled back).
    """
    from sqlalchemy import select

    from app.db.models.common import SubmissionAuditEventKind, SubmissionStatus
    from app.db.models.submission import Submission, SubmissionAuditEvent
    from app.services.upload_submission import open_job_submission

    def failing_handler(session, job, review_policy=None):
        raise RuntimeError("kaboom-before-persistence")

    monkeypatch.setitem(upload_worker._DISPATCH, UploadJobKind.thermo, failing_handler)

    with worker_db.begin():
        job = _insert_job(
            worker_db,
            kind=UploadJobKind.thermo,
            attempts=2,
            max_attempts=3,
            payload=_thermo_job_payload(),
        )
        job.created_by = _api_test_user
        submission = open_job_submission(
            worker_db,
            created_by=_api_test_user,
            job_kind=UploadJobKind.thermo,
            upload_job_id=str(job.id),
        )
        job_id = job.id
        submission_id = submission.id

    try:
        assert upload_worker._process_one_cycle() is True

        with worker_db.begin():
            worker_db.expire_all()
            assert worker_db.get(UploadJob, job_id).status == UploadJobStatus.failed
            sub = worker_db.get(Submission, submission_id)
            assert sub.status is SubmissionStatus.failed
            kinds = {
                e.event_kind
                for e in worker_db.scalars(
                    select(SubmissionAuditEvent).where(SubmissionAuditEvent.submission_id == submission_id)
                ).all()
            }
            assert SubmissionAuditEventKind.ingestion_failed in kinds
    finally:
        # Worker committed to the real DB — clean up the submission rows we
        # created (the worker_db fixture only clears upload_job).
        with worker_db.begin():
            worker_db.execute(
                text("DELETE FROM submission_audit_event WHERE submission_id = :sid"),
                {"sid": submission_id},
            )
            worker_db.execute(
                text("DELETE FROM submission WHERE id = :sid"),
                {"sid": submission_id},
            )
