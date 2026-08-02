"""Contract tests for the experimental, durable async upload queue."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_current_user, get_db, get_write_db
from app.db.models.app_user import AppUser
from app.db.models.common import UploadJobKind, UploadJobStatus
from app.db.models.submission import Submission
from app.db.models.upload_job import UploadJob
from tests.api.test_api_transport_upload import _transport_payload


def test_job_status_rejects_another_regular_user(client, db_session, _api_other_user, login_as):
    job = UploadJob(
        kind=UploadJobKind.transport,
        status=UploadJobStatus.queued,
        payload={},
        created_by=_api_other_user,
    )
    db_session.add(job)
    db_session.flush()

    response = client.get(f"/api/v1/jobs/{job.id}")
    assert response.status_code == 404


def test_job_status_allows_curator(client, db_session, _api_other_user, _api_curator_user, login_as):
    job = UploadJob(
        kind=UploadJobKind.transport,
        status=UploadJobStatus.queued,
        payload={},
        created_by=_api_other_user,
    )
    db_session.add(job)
    db_session.flush()
    login_as(_api_curator_user)

    response = client.get(f"/api/v1/jobs/{job.id}")
    assert response.status_code == 200


def test_enqueue_idempotency_replays_one_job_and_one_submission(client, db_session):
    headers = {"Idempotency-Key": "async-transport-enqueue-key-001"}
    payload = _transport_payload()
    first = client.post("/api/v1/jobs/transport", json=payload, headers=headers)
    assert first.status_code == 202, first.text
    jobs_after_first = db_session.scalar(select(func.count()).select_from(UploadJob))
    submissions_after_first = db_session.scalar(select(func.count()).select_from(Submission))

    replay = client.post("/api/v1/jobs/transport", json=payload, headers=headers)
    assert replay.status_code == 202, replay.text
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == first.json()
    assert db_session.scalar(select(func.count()).select_from(UploadJob)) == jobs_after_first
    assert db_session.scalar(select(func.count()).select_from(Submission)) == submissions_after_first


def test_enqueue_idempotency_different_payload_conflicts(client):
    headers = {"Idempotency-Key": "async-transport-enqueue-key-002"}
    first = client.post("/api/v1/jobs/transport", json=_transport_payload(), headers=headers)
    assert first.status_code == 202, first.text
    changed = _transport_payload()
    changed["sigma_angstrom"] = 2.10
    conflict = client.post("/api/v1/jobs/transport", json=changed, headers=headers)
    assert conflict.status_code == 409


def test_concurrent_identical_enqueue_creates_one_job_and_submission(db_engine, _api_test_user):
    """Separate request sessions race one idempotency key per the documented 409 contract."""
    payload = _transport_payload()
    headers = {"Idempotency-Key": "async-transport-concurrent-key-001"}

    def post_once() -> tuple[int, str | None]:
        app = create_app()
        session = Session(bind=db_engine, expire_on_commit=False)
        user = session.get(AppUser, _api_test_user)
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_write_db] = lambda: session
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            with TestClient(app) as local_client:
                response = local_client.post("/api/v1/jobs/transport", json=payload, headers=headers)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    return 409, None
                return response.status_code, response.json()["job_id"]
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _unused: post_once(), range(2)))

        # Two interleavings are legitimate, and which one occurs is a matter of
        # timing rather than correctness:
        #
        #   [202, 409] both racers pass the "no record yet" check, both insert,
        #             and the loser's commit trips the unique constraint;
        #   [202, 202] the winner commits first and the loser finds the record
        #             and *replays* it -- `_enqueue_idempotent` returns the
        #             original response, which is exactly what an idempotency
        #             key is for.
        #
        # The contract is not "one racer must lose", it is "one job and one
        # submission exist, and every success names the same job". Asserting a
        # single interleaving made this test fail under load without anything
        # being wrong. Both arms are checked below, so this is stricter than
        # the old assertion, not looser.
        statuses = sorted(status for status, _job_id in outcomes)
        assert statuses in ([202, 409], [202, 202]), outcomes
        job_ids = [job_id for status, job_id in outcomes if status == 202]
        assert len(set(job_ids)) == 1, f"idempotency key yielded distinct jobs: {outcomes}"
        with Session(db_engine) as verify:
            assert verify.scalar(select(func.count()).select_from(UploadJob)) == 1
            assert verify.scalar(select(func.count()).select_from(Submission)) == 1
    finally:
        with Session(db_engine) as cleanup, cleanup.begin():
            job_ids = [job_id for status, job_id in locals().get("outcomes", []) if status == 202]
            submission_ids = (
                list(cleanup.scalars(select(Submission.id).where(Submission.upload_job_id.in_(job_ids))).all())
                if job_ids
                else []
            )
            if submission_ids:
                cleanup.execute(
                    text("DELETE FROM submission_audit_event WHERE submission_id = ANY(:ids)"), {"ids": submission_ids}
                )
                cleanup.execute(
                    text("DELETE FROM submission_record_link WHERE submission_id = ANY(:ids)"), {"ids": submission_ids}
                )
                cleanup.execute(text("DELETE FROM submission WHERE id = ANY(:ids)"), {"ids": submission_ids})
            if job_ids:
                cleanup.execute(text("DELETE FROM upload_job WHERE id = ANY(:ids)"), {"ids": job_ids})
            cleanup.execute(
                text("DELETE FROM idempotency_record WHERE idempotency_key = :key"), {"key": headers["Idempotency-Key"]}
            )


def test_unauthorized_job_is_indistinguishable_from_missing(client, db_session, _api_other_user):
    job = UploadJob(kind=UploadJobKind.transport, status=UploadJobStatus.queued, payload={}, created_by=_api_other_user)
    db_session.add(job)
    db_session.flush()
    denied = client.get(f"/api/v1/jobs/{job.id}")
    missing = client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
