"""Two failures of one retry, racing, still make one failed submission.

The dedupe in ``record_failed_upload`` is look-up-then-insert, so two attempts
that both look before either inserts would each open a submission. The
transaction-scoped advisory lock closes that. This needs a real committed
database and two real threads, so it cannot use the per-test rollback session.

Deterministic, not timing-based: the lookup is wrapped so that after it returns,
each thread waits at a barrier for the other (bounded, so the locked case does
not hang). Without the lock both threads reach the barrier having seen nothing
and both insert -> 2 submissions. With the lock the second thread is held
outside the critical section, the first times out of the barrier, commits, and
the second then finds it -> 1 submission.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models.common import SubmissionKind
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services import upload_submission
from app.services.upload_submission import FailedUploadKey, record_failed_upload


@pytest.fixture
def committed_scratch(db_engine, _api_test_user):
    """These tests really commit; remove everything they wrote by id watermark."""
    with Session(db_engine) as probe:
        watermark = (
            probe.scalar(select(Submission.id).order_by(Submission.id.desc())) or 0
        )
    try:
        yield _api_test_user, watermark
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(
                delete(SubmissionAuditEvent).where(
                    SubmissionAuditEvent.submission_id > watermark
                )
            )
            cleanup.execute(delete(Submission).where(Submission.id > watermark))
            cleanup.commit()


def test_racing_failures_of_one_retry_open_one_submission(
    db_engine, committed_scratch, monkeypatch
) -> None:
    user_id, watermark = committed_scratch
    key = FailedUploadKey(
        route="POST /api/v1/uploads/thermo", idempotency_key="race" * 6
    )

    real_lookup = upload_submission._find_failed_submission
    barrier = threading.Barrier(2)

    def lookup_then_meet(*args, **kwargs):
        found = real_lookup(*args, **kwargs)
        try:
            barrier.wait(timeout=1.5)
        except threading.BrokenBarrierError:
            pass
        return found

    monkeypatch.setattr(upload_submission, "_find_failed_submission", lookup_then_meet)

    results: list[int | None] = []

    def attempt(n: int) -> None:
        results.append(
            record_failed_upload(
                created_by=user_id,
                kind=SubmissionKind.thermo,
                error_summary=f"attempt {n}",
                session_factory=lambda: Session(bind=db_engine),
                retry_key=key,
            )
        )

    threads = [threading.Thread(target=attempt, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)

    with Session(db_engine) as verify:
        opened = verify.scalars(
            select(Submission.id).where(Submission.id > watermark)
        ).all()
        events = verify.scalars(
            select(SubmissionAuditEvent).where(
                SubmissionAuditEvent.submission_id.in_(opened),
                SubmissionAuditEvent.details_json.is_not(None),
            )
        ).all()

    assert len(opened) == 1, "racing failures of one retry opened two submissions"
    assert len(results) == 2 and results[0] == results[1] == opened[0]
    attempts = [e.details_json["attempt"] for e in events if "attempt" in (e.details_json or {})]
    assert sorted(attempts) == [1, 2]
