"""A rolled-back request must not leave an assembled composite pending (ADR 0021, P5).

The pending composites live in ``session.info``. The worker records a failed attempt in a *fresh
transaction of the same session* after the failed one rolled back, so an entry that survived the
rollback made the commit guard refuse that bookkeeping commit: the failure was never recorded and
the job stayed ``processing`` with ``error = None`` until the reaper found it.

Reproduced through the real ``_process_one_cycle``: a handler registers a pending composite and then
fails, as ``finalize_composite_inputs`` does when an input check refuses.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

import app.workers.upload_worker as upload_worker
from app.db.composite_commit_guard import PENDING_COMPOSITE_KEY
from app.db.models.common import UploadJobKind, UploadJobStatus
from app.db.models.upload_job import UploadJob


@pytest.fixture
def worker_db(db_engine, monkeypatch) -> Iterator[Session]:
    monkeypatch.setattr(upload_worker, "SessionLocal", sessionmaker(bind=db_engine, expire_on_commit=False))
    session = Session(bind=db_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        with Session(db_engine) as cleanup:
            with cleanup.begin():
                cleanup.execute(
                    text(
                        "DELETE FROM upload_job WHERE NOT EXISTS ("
                        "SELECT 1 FROM submission WHERE submission.upload_job_id = upload_job.id)"
                    )
                )


def _job(session: Session, *, max_attempts: int) -> str:
    with session.begin():
        job = UploadJob(
            kind=UploadJobKind.thermo,
            status=UploadJobStatus.queued,
            payload={"x": 1},
            attempts=0,
            max_attempts=max_attempts,
        )
        session.add(job)
        session.flush()
        return str(job.id)


def _failing_handler_with_a_pending_composite(session, job, review_policy=None):
    session.info[PENDING_COMPOSITE_KEY] = {10**9: object()}
    raise ValueError("an input check refused")


@pytest.mark.parametrize(("max_attempts", "expected"), [(3, UploadJobStatus.queued), (1, UploadJobStatus.failed)])
def test_the_failed_attempt_is_recorded_after_a_rollback_with_a_pending_composite(
    worker_db, db_engine, monkeypatch, max_attempts, expected
):
    monkeypatch.setitem(upload_worker._DISPATCH, UploadJobKind.thermo, _failing_handler_with_a_pending_composite)
    job_id = _job(worker_db, max_attempts=max_attempts)

    assert upload_worker._process_one_cycle() is True

    with Session(db_engine) as verify:
        persisted = verify.get(UploadJob, job_id)
        assert persisted.status == expected, "the job was left in a state the failure never recorded"
        assert persisted.status is not UploadJobStatus.processing
        assert persisted.error is not None and "an input check refused" in persisted.error
        assert persisted.attempts == 1


def test_the_rollback_clears_the_pending_entries_themselves(db_engine):
    with Session(db_engine) as session:
        session.info[PENDING_COMPOSITE_KEY] = {1: object()}
        session.begin()
        session.rollback()
        assert PENDING_COMPOSITE_KEY not in session.info
        session.commit()  # nothing pending: the guard is quiet


def test_a_savepoint_rollback_keeps_what_the_outer_transaction_still_owes(db_engine):
    """Only the root rollback forgets: a savepoint undone mid-request undoes none of the composite's rows."""
    with Session(db_engine) as session:
        session.begin()
        session.info[PENDING_COMPOSITE_KEY] = {1: object()}
        nested = session.begin_nested()
        nested.rollback()
        assert PENDING_COMPOSITE_KEY in session.info
        session.rollback()
        assert PENDING_COMPOSITE_KEY not in session.info
