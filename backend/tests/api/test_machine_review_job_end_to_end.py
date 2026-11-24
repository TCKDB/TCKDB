"""Request a machine review through ``POST /api/v1/machine-review/jobs`` and run it.

The producer half of the machine-review surface: the job route queues a review,
one worker cycle asks the configured provider for an advisory review of one
submission and records the answer as a single ``submission_audit_event``.

What these tests hold down:

* **curators and admins only**: a plain user gets 403, anonymous 401, and a
  refused request writes no job and no audit event;
* **404 with the contracted detail** for a submission that does not exist;
* **a provider failure ends the job as failed**, and the attempt is still
  journalled as one ``machine_review_failed`` event;
* **no transcript or failure text in any job response**;
* **nothing but that event is written**: no status change, no moderation.

No network and no real model: the provider is handed to the worker cycle.
"""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.config import settings as app_settings
from app.db.models.common import (
    SubmissionAuditEventKind,
    SubmissionRecordType,
)
from app.db.models.submission import SubmissionAuditEvent
from app.services.machine_review.providers.fake import make_warning_result
from app.services.submission import link_record
from app.workers import machine_review_worker
from tests.api import _machine_review_seed as seed
from tests.api._machine_review_job_routes_support import anon_client, job_count, keys_anywhere  # noqa: F401

_JOBS = seed.JOBS_URL


def _body(submission_id: int, key: str = "fake") -> dict:
    return {"submission_id": submission_id, "provider_key": key}


def _review_events(db_session: Session, submission_id: int) -> list[SubmissionAuditEvent]:
    return list(
        db_session.scalars(
            select(SubmissionAuditEvent)
            .where(
                SubmissionAuditEvent.submission_id == submission_id,
                SubmissionAuditEvent.event_kind == SubmissionAuditEventKind.llm_precheck_recorded,
            )
            .order_by(SubmissionAuditEvent.id.asc())
        )
    )


@pytest.fixture(autouse=True)
def _fake_mode(monkeypatch):
    monkeypatch.setattr(app_settings, "ai_review_assistant_mode", "test")
    monkeypatch.setattr(app_settings, "deployment_mode", "local")


def _new(db_session, user_id):
    return seed.new_submission(db_session, user_id, title="machine-review job end to end")


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #


def test_a_curator_may_request_and_a_plain_user_may_not(
    client, db_session, login_as, _api_test_user, _api_curator_user
):
    submission = _new(db_session, _api_test_user)

    assert client.post(_JOBS, json=_body(submission.id)).status_code == 403  # default actor: role user
    assert job_count(db_session) == 0
    login_as(_api_curator_user)
    assert client.post(_JOBS, json=_body(submission.id)).status_code == 202
    assert job_count(db_session, submission.id) == 1


def test_anonymous_requests_are_refused_and_write_nothing(anon_client, db_session, _api_test_user):
    submission = _new(db_session, _api_test_user)

    assert anon_client.post(_JOBS, json=_body(submission.id)).status_code == 401
    assert job_count(db_session) == 0
    assert _review_events(db_session, submission.id) == []


def test_a_refused_request_runs_no_review(client, db_session, _api_test_user, monkeypatch):
    called = []

    class _Counting:
        def review_submission(self, context):
            called.append(context)
            raise AssertionError("provider must not be reached without a curator or admin")

    submission = _new(db_session, _api_test_user)
    assert client.post(_JOBS, json=_body(submission.id)).status_code == 403
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False, join_transaction_mode="create_savepoint")

    assert not machine_review_worker.process_one_cycle(factory, app_settings, heartbeat=False, provider=_Counting())
    assert called == []
    assert job_count(db_session) == 0
    assert _review_events(db_session, submission.id) == []


# --------------------------------------------------------------------------- #
# 404 and the default deployment
# --------------------------------------------------------------------------- #


def test_missing_submission_is_404_with_the_contracted_detail(client, login_as, _api_admin_user):
    login_as(_api_admin_user)

    resp = client.post(_JOBS, json=_body(999999))

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "Submission not found."
    assert body["code"] == "resource_not_found", body
    assert body["context"] == {}, body


def test_off_mode_is_refused_and_records_nothing(client, db_session, login_as, _api_admin_user, monkeypatch):
    monkeypatch.setattr(app_settings, "ai_review_assistant_mode", "off")
    submission = _new(db_session, _api_admin_user)
    login_as(_api_admin_user)

    resp = client.post(_JOBS, json=_body(submission.id))

    assert resp.status_code == 409
    assert resp.json()["code"] == "machine_review_mode_off"
    assert job_count(db_session) == 0
    assert _review_events(db_session, submission.id) == []


# --------------------------------------------------------------------------- #
# A review that succeeds
# --------------------------------------------------------------------------- #


def test_successful_run_records_one_event_and_ends_the_job(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    fixed = make_warning_result(record_type="calculation", record_ref="7")
    submission = _new(db_session, _api_admin_user)
    link_record(
        db_session, submission=submission, record_type=SubmissionRecordType.calculation, record_id=7, role="primary"
    )
    db_session.flush()
    login_as(_api_admin_user)

    job = seed.run_review(client, monkeypatch, submission.id, fixed)

    events = _review_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].details_json["format"] == {"name": "machine_review", "version": 2}
    assert job["status"] == "succeeded"
    assert job["audit_event_id"] == events[0].id
    assert job["failure_code"] is None
    assert job_count(db_session, submission.id) == 1


def test_successful_run_does_not_mutate_submission_status(client, db_session, login_as, _api_admin_user, monkeypatch):
    submission = _new(db_session, _api_admin_user)
    status_before = submission.status
    login_as(_api_admin_user)

    seed.run_review(client, monkeypatch, submission.id, seed.result())

    db_session.refresh(submission)
    assert submission.status is status_before


# --------------------------------------------------------------------------- #
# A review that fails
# --------------------------------------------------------------------------- #


def test_provider_failure_ends_the_job_failed_and_records_the_attempt(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    submission = _new(db_session, _api_admin_user)
    login_as(_api_admin_user)

    job = seed.run_review(client, monkeypatch, submission.id, expect="failed")

    assert job["failure_code"] == "internal_error"
    events = _review_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].details_json["status"] == "machine_review_failed"
    assert events[0].details_json["format"] == {"name": "machine_review", "version": 2}


def test_provider_failure_does_not_mutate_submission_status(client, db_session, login_as, _api_admin_user, monkeypatch):
    submission = _new(db_session, _api_admin_user)
    status_before = submission.status
    login_as(_api_admin_user)

    seed.run_review(client, monkeypatch, submission.id, expect="failed")

    db_session.refresh(submission)
    assert submission.status is status_before


def test_no_transcript_or_failure_text_in_any_job_response(client, db_session, login_as, _api_admin_user, monkeypatch):
    """The sync route hid the audit event id; the job read carries it by design.

    What must stay server-side is the transcript and the failure text.
    """
    submission = _new(db_session, _api_admin_user)
    login_as(_api_admin_user)
    job = seed.run_review(client, monkeypatch, submission.id, expect="failed")

    responses = [
        client.get(f"{_JOBS}/{job['id']}"),
        client.get(_JOBS, params={"submission_id": submission.id}),
    ]

    for response in responses:
        assert response.status_code == 200
        assert "upstream endpoint unreachable" not in response.text
        assert not {"detail_json", "failure_detail"} & keys_anywhere(response.json())


def test_the_synchronous_run_route_is_gone(client, login_as, _api_admin_user):
    login_as(_api_admin_user)

    assert client.post("/api/v1/admin/machine-review/run-for-submission/1").status_code in (404, 405)
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert not any("run-for-submission" in p for p in paths)
