"""Tests for ``GET /api/v1/submissions/machine-review-overview``.

A curator/admin, paginated list of submissions with each one's machine-review
state: how many runs, the latest run, and how many curator tasks are still
open. It exists beside ``/submissions/for-review`` because that route returns a
bare list with no total and scopes to pre-approval statuses only.

Reviews are seeded through the real job route, one worker cycle and the build route.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import (
    MachineReviewCuratorTaskState,
    SubmissionAuditEventKind,
    SubmissionRecordType,
    SubmissionStatus,
)
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review.schemas import (
    MachineReviewSeverity,
    MachineReviewStatus,
)
from tests.api import _machine_review_seed as seed
from tests.services.scientific_read._factories import make_species

_URL = "/api/v1/submissions/machine-review-overview"


@pytest.fixture
def anon_client(db_session: Session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def _reviewed_submission(client, db_session, monkeypatch, admin_id, *, severity=MachineReviewSeverity.warning):
    species = make_species(db_session)
    submission = seed.new_submission(db_session, admin_id)
    seed.link(db_session, submission, SubmissionRecordType.species, species.id)
    seed.run_review(
        client, monkeypatch, submission.id,
        seed.result((seed.finding("species", species.id, severity=severity),)),
    )
    return submission, species


def _by_id(body) -> dict[int, dict]:
    return {i["id"]: i for i in body["items"]}


def test_overview_is_gated(client, anon_client, login_as, _api_curator_user, _api_admin_user):
    assert client.get(_URL).status_code == 403  # default actor: role=user
    assert anon_client.get(_URL).status_code == 401
    for user in (_api_curator_user, _api_admin_user):
        login_as(user)
        assert client.get(_URL).status_code == 200


def test_route_is_not_swallowed_by_the_submission_id_route(client, login_as, _api_curator_user):
    login_as(_api_curator_user)
    resp = client.get(_URL)
    assert resp.status_code == 200
    assert set(resp.json()) == {"items", "total", "skip", "limit"}


def test_exact_total_with_limit_below_total(client, db_session, login_as, _api_admin_user, _api_curator_user):
    for _ in range(3):
        seed.new_submission(db_session, _api_admin_user)
    login_as(_api_curator_user)

    body = client.get(_URL, params={"limit": 2}).json()

    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["skip"] == 0
    second = client.get(_URL, params={"limit": 2, "offset": 2}).json()
    assert len(second["items"]) == 1 and second["skip"] == 2
    assert {i["id"] for i in body["items"]}.isdisjoint({i["id"] for i in second["items"]})


def test_filters_by_status_reviewed_and_open_tasks(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    login_as(_api_admin_user)
    plain = seed.new_submission(db_session, _api_admin_user)
    reviewed, _ = _reviewed_submission(client, db_session, monkeypatch, _api_admin_user)
    tasked, _ = _reviewed_submission(client, db_session, monkeypatch, _api_admin_user)
    seed.build_tasks(client, tasked.id)
    flagged, _ = _reviewed_submission(client, db_session, monkeypatch, _api_admin_user)
    flagged.status = SubmissionStatus.precheck_passed
    db_session.flush()
    login_as(_api_curator_user)

    def ids(**params):
        body = client.get(_URL, params=params).json()
        assert body["total"] == len(body["items"])
        return {i["id"] for i in body["items"]}

    assert ids() == {plain.id, reviewed.id, tasked.id, flagged.id}
    assert ids(reviewed="true") == {reviewed.id, tasked.id, flagged.id}
    assert ids(reviewed="false") == {plain.id}
    assert ids(has_open_tasks="true") == {tasked.id}
    assert ids(has_open_tasks="false") == {plain.id, reviewed.id, flagged.id}
    assert ids(statuses="precheck_passed") == {flagged.id}
    assert ids(statuses=["precheck_passed", "pending"]) == {plain.id, reviewed.id, tasked.id, flagged.id}
    assert ids(reviewed="true", has_open_tasks="true", statuses="pending") == {tasked.id}


def test_latest_run_is_the_newest_event_and_null_when_unreviewed(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    login_as(_api_admin_user)
    unreviewed = seed.new_submission(db_session, _api_admin_user)
    submission, species = _reviewed_submission(client, db_session, monkeypatch, _api_admin_user)
    seed.run_review(
        client, monkeypatch, submission.id,
        seed.result(
            (
                seed.finding("species", species.id, severity=MachineReviewSeverity.info),
                seed.finding("species", species.id, severity=MachineReviewSeverity.critical, message="Bad."),
            ),
            status=MachineReviewStatus.machine_screened_needs_attention,
        ),
    )
    login_as(_api_curator_user)

    items = _by_id(client.get(_URL).json())

    assert items[unreviewed.id]["machine_review"] == {
        "run_count": 0, "latest_run": None, "open_job": None, "open_task_count": 0, "task_count": 0,
    }
    mr = items[submission.id]["machine_review"]
    assert mr["run_count"] == 2
    run = mr["latest_run"]
    assert run["status"] == "machine_screened_needs_attention"
    assert run["highest_severity"] == "critical"
    assert run["findings_count"] == 2
    assert run["parsed"] is True
    assert run["model"] == "fake_test/machine-review"
    assert run["provider"] == "FakeMachineReviewProvider"
    assert run["reviewed_at"] is not None
    # The newest event, not the first: the inspection names the same event.
    login_as(_api_admin_user)
    runs = client.get(seed.INSPECT_URL.format(submission.id)).json()["runs"]
    assert run["audit_event_id"] == runs[0]["audit_event_id"]


def test_a_stored_previous_format_run_is_unparsed_and_its_current_twin_is_parsed(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    login_as(_api_admin_user)
    previous_format, species = _reviewed_submission(client, db_session, monkeypatch, _api_admin_user)
    current_format, _ = _reviewed_submission(client, db_session, monkeypatch, _api_admin_user)
    stored = db_session.scalars(
        select(SubmissionAuditEvent).where(
            SubmissionAuditEvent.submission_id == previous_format.id,
            SubmissionAuditEvent.event_kind == SubmissionAuditEventKind.llm_precheck_recorded,
        )
    ).one()
    stored.details_json = {**stored.details_json, "format": {"name": "machine_review", "version": 1}}
    db_session.flush()
    login_as(_api_curator_user)

    items = _by_id(client.get(_URL).json())

    assert items[previous_format.id]["machine_review"]["latest_run"]["parsed"] is False
    assert items[current_format.id]["machine_review"]["latest_run"]["parsed"] is True


def test_latest_run_follows_created_at_before_the_id(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    login_as(_api_admin_user)
    submission, species = _reviewed_submission(client, db_session, monkeypatch, _api_admin_user)
    seed.run_review(
        client, monkeypatch, submission.id,
        seed.result((seed.finding("species", species.id, severity=MachineReviewSeverity.critical),)),
    )
    first, second = db_session.scalars(
        select(SubmissionAuditEvent)
        .where(SubmissionAuditEvent.submission_id == submission.id)
        .where(SubmissionAuditEvent.event_kind == SubmissionAuditEventKind.llm_precheck_recorded)
        .order_by(SubmissionAuditEvent.id)
    ).all()
    first.created_at = second.created_at + timedelta(hours=1)
    db_session.flush()
    login_as(_api_curator_user)

    run = _by_id(client.get(_URL).json())[submission.id]["machine_review"]["latest_run"]

    assert run["audit_event_id"] == first.id
    assert run["highest_severity"] == "warning"


def test_a_failed_run_shows_as_failed(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    login_as(_api_admin_user)
    submission = seed.new_submission(db_session, _api_admin_user)

    seed.run_review(client, monkeypatch, submission.id, expect="failed")
    login_as(_api_curator_user)

    run = _by_id(client.get(_URL).json())[submission.id]["machine_review"]["latest_run"]

    assert run["status"] == "machine_review_failed"
    assert run["highest_severity"] is None
    assert run["findings_count"] == 0


def test_open_task_count_excludes_terminal_tasks(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    login_as(_api_admin_user)
    species = make_species(db_session)
    submission = seed.new_submission(db_session, _api_admin_user)
    seed.link(db_session, submission, SubmissionRecordType.species, species.id)
    seed.run_review(
        client, monkeypatch, submission.id,
        seed.result((
            seed.finding("species", species.id, message="One."),
            seed.finding("species", species.id, message="Two."),
        )),
    )
    task_ids = seed.build_tasks(client, submission.id)["task_ids"]
    assert client.post(
        f"{seed.TASKS_URL}/{task_ids[0]}/resolve",
        json={
            "resolution_state": MachineReviewCuratorTaskState.dismissed_machine_finding.value,
            "resolution_note": "n",
        },
    ).status_code == 200
    login_as(_api_curator_user)

    mr = _by_id(client.get(_URL).json())[submission.id]["machine_review"]

    assert mr["task_count"] == 2
    assert mr["open_task_count"] == 1


def test_item_fields_and_order(client, db_session, login_as, _api_admin_user, _api_curator_user):
    older = seed.new_submission(db_session, _api_admin_user, title="older")
    newer = seed.new_submission(db_session, _api_admin_user, title="newer")
    same_a = seed.new_submission(db_session, _api_admin_user, title="same a")
    same_b = seed.new_submission(db_session, _api_admin_user, title="same b")
    base = datetime(2026, 1, 1, 12, 0, 0)
    older.submitted_at = base
    newer.submitted_at = base + timedelta(days=1)
    same_a.submitted_at = same_b.submitted_at = base + timedelta(days=2)
    db_session.flush()
    login_as(_api_curator_user)

    body = client.get(_URL).json()

    ids = [i["id"] for i in body["items"]]
    assert ids == [max(same_a.id, same_b.id), min(same_a.id, same_b.id), newer.id, older.id]
    item = _by_id(body)[older.id]
    assert set(item) == {
        "id", "public_ref", "status", "submission_kind", "title", "created_by",
        "created_by_username", "submitted_at", "machine_review",
    }
    assert item["created_by"] == _api_admin_user
    assert item["created_by_username"] == "testadmin"
    assert item["title"] == "older"
    assert item["public_ref"] == older.public_ref
    assert item["status"] == "pending"


def test_overview_does_not_cost_more_queries_for_a_longer_page(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    def _seed(n: int) -> None:
        login_as(_api_admin_user)
        for _ in range(n):
            sub, _ = _reviewed_submission(client, db_session, monkeypatch, _api_admin_user)
            seed.build_tasks(client, sub.id)

    def _count() -> int:
        login_as(_api_curator_user)
        statements = 0
        engine = db_session.connection().engine

        def _before(conn, cursor, statement, parameters, context, executemany):
            nonlocal statements
            statements += 1

        event.listen(engine, "before_cursor_execute", _before)
        try:
            body = client.get(_URL, params={"limit": 200}).json()
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        assert all(i["machine_review"]["open_task_count"] == 1 for i in body["items"])
        return statements

    _seed(2)
    two = _count()
    _seed(8)
    ten = _count()
    assert ten == two, f"the overview cost {two} statements for 2 submissions and {ten} for 10"


def test_overview_writes_nothing(client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch):
    login_as(_api_admin_user)
    sub, _ = _reviewed_submission(client, db_session, monkeypatch, _api_admin_user)
    before = sub.status
    login_as(_api_curator_user)
    assert client.get(_URL).status_code == 200
    db_session.refresh(sub)
    assert sub.status is before
    assert db_session.get(Submission, sub.id) is sub


_OPEN_JOB_FIELDS = {"id", "status", "step_count", "cancel_requested", "created_at", "started_at"}


def _queue(client, submission_id):
    resp = client.post(seed.JOBS_URL, json={"submission_id": submission_id, "provider_key": "fake"})
    assert resp.status_code == 202, resp.text
    return resp.json()


def test_a_queued_job_is_the_submissions_open_job(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    monkeypatch.setattr("app.api.config.settings.ai_review_assistant_mode", "test")
    monkeypatch.setattr("app.api.config.settings.deployment_mode", "local")
    login_as(_api_curator_user)
    queued = seed.new_submission(db_session, _api_admin_user, title="queued")
    idle = seed.new_submission(db_session, _api_admin_user, title="idle")
    job = _queue(client, queued.id)

    items = _by_id(client.get(_URL).json())

    open_job = items[queued.id]["machine_review"]["open_job"]
    assert set(open_job) == _OPEN_JOB_FIELDS
    assert open_job["id"] == job["id"]
    assert open_job["status"] == "queued"
    assert open_job["step_count"] == 0
    assert open_job["started_at"] is None
    assert items[idle.id]["machine_review"]["open_job"] is None


def test_a_finished_job_is_no_longer_open(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    login_as(_api_admin_user)
    submission = seed.new_submission(db_session, _api_admin_user)
    seed.run_review(client, monkeypatch, submission.id, seed.result())
    login_as(_api_curator_user)

    mr = _by_id(client.get(_URL).json())[submission.id]["machine_review"]

    assert mr["open_job"] is None
    assert mr["run_count"] == 1


def test_open_jobs_do_not_cost_more_queries_for_a_longer_page(
    client, db_session, login_as, _api_admin_user, _api_curator_user, monkeypatch
):
    monkeypatch.setattr("app.api.config.settings.ai_review_assistant_mode", "test")
    monkeypatch.setattr("app.api.config.settings.deployment_mode", "local")
    subs = [seed.new_submission(db_session, _api_admin_user, title=f"s{i}") for i in range(5)]
    login_as(_api_curator_user)

    def _count(limit: int) -> int:
        statements = []
        engine = db_session.connection().engine

        def _before(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _before)
        try:
            assert client.get(_URL, params={"limit": limit}).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        return len(statements)

    for sub in subs[:3]:
        _queue(client, sub.id)
    assert _count(1) == _count(5)
