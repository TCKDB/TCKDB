"""Tests for ``POST /api/v1/admin/machine-review/run-for-submission/{id}``.

The admin-triggered producer half of the machine-review surface: it asks the
configured provider for an advisory review of one submission and records the
answer as a single ``submission_audit_event``. Its sibling,
``curator-tasks/build-for-submission``, is the consumer that turns those events
into curator tasks.

What these tests hold down:

* **admin only**, like every other route on this router;
* **404 with the contracted detail** for a submission that does not exist;
* **a provider failure is a 200**, carrying ``machine_review_failed`` and a
  reason -- never a 5xx. The layer is advisory: the server did its job by
  asking and journalling the answer, and "the reviewer could not review" is an
  answer a curator needs, not an error to retry past. A 5xx would also throw
  away the audit event the failure just wrote;
* **no internal row ids in the body** (DR-0028 Requirement 2) -- in particular
  not the id of the event it wrote;
* **nothing but that event is written**: no status change, no moderation.

No network and no real model. The provider is substituted at the factory the
runner calls, which leaves the real route, the real runner, the real failure
conversion and the real audit write in the path -- only the transport is
replaced.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.config import Settings
from app.api.deps import get_db, get_write_db
from app.db.models.common import SubmissionAuditEventKind, SubmissionKind
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review.providers.fake import (
    build_fake_machine_review_provider,
    make_warning_result,
)
from app.services.submission import create_submission

_BASE = "/api/v1/admin/machine-review/run-for-submission"

#: The response contract, fixed: a frontend is coded against exactly this set.
_RESPONSE_FIELDS = {
    "submission_id",
    "status",
    "findings_count",
    "summary",
    "model",
    "provider",
    "audit_event_recorded",
    "failure_reason",
}


@pytest.fixture
def anon_client(db_session: Session):
    """A client with DB overrides but no auth override -> real auth runs."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


class _RaisingProvider:
    """Stands in for a transport that cannot reach the model."""

    def review_submission(self, context):
        """Fail the way a dead endpoint does."""
        raise RuntimeError("upstream endpoint unreachable")


def _use_provider(monkeypatch, provider) -> None:
    """Make the runner's factory hand back ``provider``.

    Patched at the runner rather than at the route so the route, the runner,
    the failure conversion and the audit write are all the real ones.
    """
    monkeypatch.setattr(
        "app.services.machine_review.run.build_machine_review_provider",
        lambda settings_obj: provider,
    )


def _new_submission(db_session: Session, user_id: int) -> Submission:
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.thermo,
        title="admin machine-review run",
        summary="compact",
    )
    db_session.flush()
    return submission


def _review_events(
    db_session: Session, submission_id: int
) -> list[SubmissionAuditEvent]:
    return list(
        db_session.scalars(
            select(SubmissionAuditEvent)
            .where(
                SubmissionAuditEvent.submission_id == submission_id,
                SubmissionAuditEvent.event_kind
                == SubmissionAuditEventKind.llm_precheck_recorded,
            )
            .order_by(SubmissionAuditEvent.id.asc())
        )
    )


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #


def test_run_requires_admin(client, login_as, _api_curator_user):
    """A plain user and a curator are both refused; this is an admin surface."""
    assert client.post(f"{_BASE}/1").status_code == 403  # default actor: role=user
    login_as(_api_curator_user)
    assert client.post(f"{_BASE}/1").status_code == 403


def test_run_requires_authentication(anon_client):
    """Anonymous callers never reach the provider."""
    assert anon_client.post(f"{_BASE}/1").status_code == 401


def test_refused_request_runs_no_review(client, db_session, _api_test_user, monkeypatch):
    """A 403 writes nothing: the gate is in front of the provider, not behind it."""
    called = []

    class _Counting:
        def review_submission(self, context):
            called.append(context)
            raise AssertionError("provider must not be reached without admin")

    _use_provider(monkeypatch, _Counting())
    submission = _new_submission(db_session, _api_test_user)

    assert client.post(f"{_BASE}/{submission.id}").status_code == 403
    assert called == []
    assert _review_events(db_session, submission.id) == []


# --------------------------------------------------------------------------- #
# 404
# --------------------------------------------------------------------------- #


def test_missing_submission_is_404_with_the_contracted_detail(
    client, login_as, _api_admin_user
):
    """The 404 says what is missing, in the words the contract fixes.

    The code is ``http_404`` because this route names a missing submission the
    same way its sibling ``curator-tasks/build-for-submission`` does -- a bare
    ``HTTPException`` with that detail. That is deliberate agreement, not an
    oversight: the two routes meet the identical condition on the identical
    surface, and this repo has already paid once for one condition answering
    two codes (see ``test_every_curator_task_route_names_a_missing_task_the_
    same_way``). If a named ``submission_not_found`` code is ever introduced,
    both routes move together and both assertions here change.
    """
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/999999")

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "Submission not found."
    assert body["code"] == "http_404", body
    # DR-0028 Req. 2: 999999 is the caller's own path parameter; nothing
    # server-side is echoed back alongside it.
    assert body["context"] == {}, body


# --------------------------------------------------------------------------- #
# The default deployment: off mode
# --------------------------------------------------------------------------- #


def test_off_mode_returns_not_run_and_records_nothing(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    """With no provider configured the run is a well-formed 200 saying so.

    ``AI_REVIEW_ASSISTANT_MODE=off`` is what an unconfigured deployment runs,
    and this is what it gets: a definite ``not_run``, not an error and not a
    silently empty body. The real factory is in the path -- only the settings
    it reads are pinned.

    Pinned rather than inherited on purpose. Reading the ambient settings
    would make this test's meaning depend on the machine it runs on, and on a
    developer box with cloud mode configured it would reach for a real
    endpoint -- which no test here may ever do.
    """
    monkeypatch.setattr(
        "app.services.machine_review.run.app_settings",
        Settings(ai_review_assistant_mode="off"),
    )
    submission = _new_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/{submission.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _RESPONSE_FIELDS
    assert body["submission_id"] == submission.id
    assert body["status"] == "not_run"
    assert body["findings_count"] == 0
    assert body["audit_event_recorded"] is False
    assert body["failure_reason"] is None
    assert _review_events(db_session, submission.id) == []


# --------------------------------------------------------------------------- #
# A review that succeeds
# --------------------------------------------------------------------------- #


def test_successful_run_returns_the_review_and_records_it(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    """The advisory result comes back whole, and one event lands."""
    fixed = make_warning_result(record_type="calculation", record_ref="7")
    _use_provider(monkeypatch, build_fake_machine_review_provider(fixed))
    submission = _new_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/{submission.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _RESPONSE_FIELDS
    assert body["status"] == "machine_screened_warning"
    assert body["findings_count"] == 1
    assert body["summary"] == fixed.summary
    assert body["model"] == fixed.model
    assert body["provider"] == fixed.provider
    assert body["audit_event_recorded"] is True
    assert body["failure_reason"] is None

    events = _review_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].details_json["schema_version"] == "machine_review_v2"


def test_successful_run_does_not_mutate_submission_status(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    """Machine review is a third axis: it moves no submission through its states."""
    _use_provider(monkeypatch, build_fake_machine_review_provider())
    submission = _new_submission(db_session, _api_admin_user)
    status_before = submission.status
    login_as(_api_admin_user)

    assert client.post(f"{_BASE}/{submission.id}").status_code == 200

    db_session.refresh(submission)
    assert submission.status is status_before


def test_response_carries_no_internal_row_id(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    """DR-0028 Requirement 2: the audit event's id stays server-side.

    ``audit_event_recorded`` is the actionable fact; which row it landed in is
    not, and a body that named it would invite a client to address it.
    """
    _use_provider(monkeypatch, build_fake_machine_review_provider())
    submission = _new_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)

    body = client.post(f"{_BASE}/{submission.id}").json()

    events = _review_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].id is not None
    assert set(body) == _RESPONSE_FIELDS
    assert "audit_event_id" not in body
    assert events[0].id not in body.values()


# --------------------------------------------------------------------------- #
# A review that fails
# --------------------------------------------------------------------------- #


def test_provider_failure_is_200_not_5xx(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    """A dead provider is an advisory answer, not a server error."""
    _use_provider(monkeypatch, _RaisingProvider())
    submission = _new_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/{submission.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _RESPONSE_FIELDS
    assert body["status"] == "machine_review_failed"
    assert body["findings_count"] == 0
    assert body["audit_event_recorded"] is True
    assert body["failure_reason"] is not None
    assert "RuntimeError" in body["failure_reason"]


def test_provider_failure_still_records_the_attempt(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    """The failure is journalled, which is why it must not be turned into a 5xx.

    A ``get_write_db`` route that raised would roll the event back, and the one
    durable trace that the reviewer was asked and could not answer would be
    gone.
    """
    _use_provider(monkeypatch, _RaisingProvider())
    submission = _new_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)

    client.post(f"{_BASE}/{submission.id}")

    events = _review_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].details_json["status"] == "machine_review_failed"
    assert events[0].details_json["schema_version"] == "machine_review_v2"


def test_provider_failure_does_not_mutate_submission_status(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    """A failed review is as harmless to the submission as a successful one."""
    _use_provider(monkeypatch, _RaisingProvider())
    submission = _new_submission(db_session, _api_admin_user)
    status_before = submission.status
    login_as(_api_admin_user)

    assert client.post(f"{_BASE}/{submission.id}").status_code == 200

    db_session.refresh(submission)
    assert submission.status is status_before
