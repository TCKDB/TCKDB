"""Routes under ``/api/v1/machine-review/jobs``: request, poll, list and cancel a machine review."""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.config import settings as app_settings
from app.db.models.common import (
    MachineReviewJobEventKind,
    MachineReviewJobStatus,
)
from app.db.models.machine_review_job import MachineReviewJob, MachineReviewJobEvent
from app.services.machine_review import jobs
from tests.api._machine_review_job_routes_support import (  # noqa: F401
    JOBS,
    PROVIDERS,
    anon_client,
    fake_mode,
    job_count,
    keys_anywhere,
    submission,
)

pytestmark = pytest.mark.usefixtures("fake_mode")


def _post(client, submission, key="fake"):
    return client.post(JOBS, json={"submission_id": submission.id, "provider_key": key})


def test_second_request_is_a_conflict_without_ids(client, db_session, login_as, _api_curator_user, submission):
    login_as(_api_curator_user)
    first = _post(client, submission)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "queued"

    second = _post(client, submission)

    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "machine_review_job_conflict"
    assert body["context"]["open_job"]["created_at"] == first.json()["created_at"]
    assert not {k for k in keys_anywhere(body) if k == "id" or k.endswith("_id") or k.endswith("_pk")}
    assert job_count(db_session, submission.id) == 1


def test_curator_and_admin_succeed_where_a_plain_user_and_anonymous_are_refused(
    client, anon_client, db_session, login_as, _api_curator_user, _api_admin_user, submission
):
    assert _post(client, submission).status_code == 403  # default actor has role user
    assert _post(anon_client, submission).status_code == 401
    assert job_count(db_session) == 0
    login_as(_api_curator_user)
    assert _post(client, submission).status_code == 202
    login_as(_api_admin_user)
    assert client.get(JOBS, params={"submission_id": submission.id}).status_code == 200


@pytest.mark.parametrize("method,path", [("get", f"{JOBS}/1"), ("get", JOBS), ("post", f"{JOBS}/1/cancel"), ("get", PROVIDERS)])
def test_other_job_routes_refuse_a_plain_user_and_anonymous(client, anon_client, method, path):
    assert getattr(client, method)(path).status_code == 403
    assert getattr(anon_client, method)(path).status_code == 401


def test_unknown_submission_is_404(client, login_as, _api_curator_user):
    login_as(_api_curator_user)
    response = client.post(JOBS, json={"submission_id": 999999, "provider_key": "fake"})
    assert response.status_code == 404
    assert client.get(JOBS, params={"submission_id": 999999}).status_code == 404


def test_request_body_is_strict(client, login_as, _api_curator_user, submission):
    login_as(_api_curator_user)
    bad = {"submission_id": submission.id, "provider_key": "fake", "extra": 1}
    assert client.post(JOBS, json=bad).status_code == 422
    assert client.post(JOBS, json={"submission_id": submission.id, "provider_key": "nope"}).status_code == 422
    for bad_id in (2**70, 0, -1):
        assert client.post(JOBS, json={"submission_id": bad_id, "provider_key": "fake"}).status_code == 422


def _running_job_with_events(db_session, submission, user_id, n=5):
    job = MachineReviewJob(
        submission_id=submission.id,
        requested_by=user_id,
        provider_key="fake",
        executor="in_process",
        status=MachineReviewJobStatus.running,
    )
    db_session.add(job)
    db_session.flush()
    for _ in range(n):
        jobs.append_event(db_session, job, MachineReviewJobEventKind.model_turn, outcome="ok")
    return job


def test_after_seq_returns_only_later_events_in_order(client, db_session, login_as, _api_curator_user, submission):
    login_as(_api_curator_user)
    job = _running_job_with_events(db_session, submission, _api_curator_user)

    body = client.get(f"{JOBS}/{job.id}", params={"after_seq": 2}).json()

    assert [e["seq"] for e in body["events"]] == [3, 4, 5]
    assert [e["seq"] for e in client.get(f"{JOBS}/{job.id}").json()["events"]] == [1, 2, 3, 4, 5]
    assert client.get(f"{JOBS}/{job.id}", params={"after_seq": -1}).status_code == 422


def test_transcripts_and_failure_detail_never_leave_the_server(
    client, db_session, login_as, _api_curator_user, submission, monkeypatch
):
    monkeypatch.setattr(app_settings, "llm_precheck_store_full_context", True)
    login_as(_api_curator_user)
    sentinel = "SENTINEL-PROMPT-TEXT"
    job = _running_job_with_events(db_session, submission, _api_curator_user, n=1)
    jobs.append_event(
        db_session, job, MachineReviewJobEventKind.model_turn, detail={"prompt": sentinel}
    )
    job.failure_detail = sentinel
    db_session.flush()
    stored = db_session.scalars(
        select(MachineReviewJobEvent.detail_json).where(MachineReviewJobEvent.job_id == job.id)
    ).all()
    assert {"prompt": sentinel} in stored and job.failure_detail == sentinel

    for response in (
        client.get(f"{JOBS}/{job.id}"),
        client.get(JOBS, params={"submission_id": submission.id}),
    ):
        assert response.status_code == 200
        assert sentinel not in response.text
        assert not {"detail_json", "failure_detail", "budget_json", "context_hash"} & keys_anywhere(response.json())


def test_list_puts_the_open_job_first(client, db_session, login_as, _api_curator_user, submission):
    login_as(_api_curator_user)
    first = _post(client, submission).json()
    assert client.post(f"{JOBS}/{first['id']}/cancel").status_code == 200
    second = _post(client, submission).json()

    body = client.get(JOBS, params={"submission_id": submission.id}).json()

    assert [i["id"] for i in body["items"]] == [second["id"], first["id"]]
    assert body["total"] == 2


def test_cancel_queued_running_terminal_and_unknown(client, db_session, login_as, _api_curator_user, submission):
    login_as(_api_curator_user)
    queued = _post(client, submission).json()
    cancelled = client.post(f"{JOBS}/{queued['id']}/cancel")
    assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled")

    again = client.post(f"{JOBS}/{queued['id']}/cancel")
    assert again.status_code == 409 and again.json()["code"] == "machine_review_job_not_cancellable"
    assert db_session.get(MachineReviewJob, queued["id"]).status is MachineReviewJobStatus.cancelled

    running = _running_job_with_events(db_session, submission, _api_curator_user, n=0)
    response = client.post(f"{JOBS}/{running.id}/cancel")
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["cancel_requested"]) == ("running", True)

    missing = client.post(f"{JOBS}/999999/cancel")
    assert missing.status_code == 404 and missing.json()["code"] == "machine_review_job_not_found"
    assert client.get(f"{JOBS}/999999").json()["code"] == "machine_review_job_not_found"
