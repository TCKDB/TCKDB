"""A job's own bookkeeping must not be able to destroy the science it ran.

``job.result`` is presentational — it is what the polling client renders — and
it is arbitrary handler output going into a ``JSONB`` column. A value
PostgreSQL will not store therefore fails **deterministically**. Sharing the
persistence transaction, that meant every retry re-ran the whole ingestion and
failed identically on the same value until ``max_attempts`` was exhausted, so
a presentational field permanently prevented the science from ever being
stored while the client politely polled a job that could never succeed.

The terminal job state is deliberately *not* isolated alongside it. That state
is the exactly-once fence: science committed under a still-claimable job would
be re-run by the reaper and duplicated. Only the rendering of the outcome is
bookkeeping; the fence belongs to the primary unit.

Every assertion here reads from a session opened *after* the worker's own
commit, so it sees committed state rather than a live identity map.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

import app.workers.upload_worker as upload_worker
from app.db.models.common import (
    MoleculeKind,
    StereoKind,
    UploadJobKind,
    UploadJobStatus,
)
from app.db.models.species import Species
from app.db.models.upload_job import UploadJob

#: Rejected by ``jsonb`` in every server encoding — the encoding-independent
#: stand-in for "this result body cannot be stored".
UNSTORABLE_RESULT = {"type": "thermo", "note": "bad" + chr(0) + "value"}

SMILES = "[He]workerisol"


@pytest.fixture
def worker_db(db_engine, monkeypatch) -> Iterator[Session]:
    """A session on the real test engine, with the worker pointed at it.

    ``_process_one_cycle`` opens and commits its own sessions from
    ``upload_worker.SessionLocal``; redirecting that is what lets the test
    read back what the worker actually committed. Jobs and the scientific rows
    the stub handler writes are removed on teardown.
    """
    monkeypatch.setattr(
        upload_worker,
        "SessionLocal",
        sessionmaker(bind=db_engine, expire_on_commit=False),
    )

    session = Session(bind=db_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        with Session(db_engine) as cleanup:
            with cleanup.begin():
                cleanup.execute(
                    text("DELETE FROM species WHERE smiles = :s"), {"s": SMILES}
                )
                cleanup.execute(
                    text(
                        "DELETE FROM upload_job WHERE NOT EXISTS ("
                        "SELECT 1 FROM submission "
                        "WHERE submission.upload_job_id = upload_job.id)"
                    )
                )


def _insert_job(session: Session, *, max_attempts: int = 3) -> UploadJob:
    job = UploadJob(
        kind=UploadJobKind.thermo,
        status=UploadJobStatus.queued,
        payload={"x": 1},
        attempts=0,
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()
    return job


def _science_writing_handler(result):
    """A stub handler that persists a real scientific row and returns *result*."""

    def handler(session, job, review_policy=None):
        session.add(
            Species(
                kind=MoleculeKind.molecule,
                smiles=SMILES,
                inchi_key="SWQJXJOGLNCZEY-UHFFFAOYSA-N",
                charge=0,
                multiplicity=1,
                stereo_kind=StereoKind.unspecified,
            )
        )
        return result

    return handler


def _install(monkeypatch, result) -> None:
    monkeypatch.setitem(
        upload_worker._DISPATCH,
        UploadJobKind.thermo,
        _science_writing_handler(result),
    )


class TestUnstorableResult:
    def test_the_science_survives_and_the_job_still_completes(
        self, worker_db, db_engine, monkeypatch
    ) -> None:
        _install(monkeypatch, UNSTORABLE_RESULT)

        with worker_db.begin():
            job_id = _insert_job(worker_db).id

        assert upload_worker._process_one_cycle() is True

        with Session(db_engine) as verify:
            assert verify.execute(
                text("SELECT id FROM species WHERE smiles = :s"), {"s": SMILES}
            ).first() is not None, (
                "an unstorable job.result destroyed the science the job persisted"
            )

            persisted = verify.get(UploadJob, job_id)
            # The exactly-once fence still landed: a job left claimable would
            # be re-leased by the reaper and duplicate the science.
            assert persisted.status == UploadJobStatus.complete
            assert persisted.completed_at is not None
            assert persisted.lease_expires_at is None
            assert persisted.error is None
            # ...carrying a stand-in that says what happened.
            assert persisted.result is not None
            assert persisted.result.get("result_unavailable") is True
            assert persisted.result.get("reason")

    def test_it_does_not_burn_the_retry_budget(
        self, worker_db, db_engine, monkeypatch
    ) -> None:
        """A deterministic failure must not be retried into exhaustion."""
        _install(monkeypatch, UNSTORABLE_RESULT)

        with worker_db.begin():
            job_id = _insert_job(worker_db, max_attempts=3).id

        upload_worker._process_one_cycle()

        with Session(db_engine) as verify:
            persisted = verify.get(UploadJob, job_id)
            assert persisted.status is not UploadJobStatus.queued
            assert persisted.status is not UploadJobStatus.failed
            assert persisted.attempts == 1

    def test_the_failure_is_logged_loudly(
        self, worker_db, db_engine, monkeypatch, caplog
    ) -> None:
        import logging

        _install(monkeypatch, UNSTORABLE_RESULT)

        with worker_db.begin():
            _insert_job(worker_db)

        with caplog.at_level(logging.ERROR, logger="app.workers.upload_worker"):
            upload_worker._process_one_cycle()

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors
        assert "result could not be stored" in " ".join(
            r.getMessage() for r in errors
        )


class TestStorableResultUnchanged:
    def test_the_handler_result_is_stored_verbatim(
        self, worker_db, db_engine, monkeypatch
    ) -> None:
        expected = {"type": "thermo", "id": 4321}
        _install(monkeypatch, expected)

        with worker_db.begin():
            job_id = _insert_job(worker_db).id

        assert upload_worker._process_one_cycle() is True

        with Session(db_engine) as verify:
            persisted = verify.get(UploadJob, job_id)
            assert persisted.status == UploadJobStatus.complete
            assert persisted.result == expected
            assert verify.execute(
                text("SELECT id FROM species WHERE smiles = :s"), {"s": SMILES}
            ).first() is not None
