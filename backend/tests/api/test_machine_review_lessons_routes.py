"""Routes for curator-written machine-review lessons: who may call each, payload limits and error bodies."""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models.common import MachineReviewLessonStatus as S
from app.db.models.machine_review_lesson import MachineReviewLesson
from tests.api._machine_review_job_routes_support import anon_client  # noqa: F401

CURATOR = "/api/v1/machine-review/lessons"
ADMIN = "/api/v1/admin/machine-review/lessons"


def _count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(MachineReviewLesson))


def _body(**kw) -> dict:
    return {"text": "Check the spin multiplicity first.", "category": "thermo", **kw}


def _row(db_session, lesson_id: int) -> MachineReviewLesson:
    db_session.expire_all()
    return db_session.get(MachineReviewLesson, lesson_id)


def test_a_curator_and_an_admin_may_post_and_a_plain_user_and_anonymous_may_not(
    client, anon_client, db_session, login_as, _api_curator_user, _api_admin_user
):
    assert client.post(CURATOR, json=_body()).status_code == 403
    assert anon_client.post(CURATOR, json=_body()).status_code == 401
    assert _count(db_session) == 0

    login_as(_api_curator_user)
    created = client.post(CURATOR, json=_body())
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "proposed" and created.json()["signal"] == "curator_flagged_missed_issue"
    assert _count(db_session) == 1
    login_as(_api_admin_user)
    assert client.post(CURATOR, json=_body(text="Another.")).status_code == 201
    assert _count(db_session) == 2


def test_a_curator_cannot_list_edit_verify_or_retire_and_the_row_is_unchanged(
    client, db_session, login_as, _api_curator_user, _api_admin_user
):
    login_as(_api_curator_user)
    lesson_id = client.post(CURATOR, json=_body()).json()["id"]
    assert client.get(ADMIN).status_code == 403
    assert client.patch(f"{ADMIN}/{lesson_id}", json={"text": "Changed."}).status_code == 403
    assert client.post(f"{ADMIN}/{lesson_id}/verify").status_code == 403
    assert client.post(f"{ADMIN}/{lesson_id}/retire").status_code == 403
    row = _row(db_session, lesson_id)
    assert (row.status, row.text, row.verified_by, row.retired_by) == (S.proposed, _body()["text"], None, None)

    login_as(_api_admin_user)
    verified = client.post(f"{ADMIN}/{lesson_id}/verify")
    assert verified.status_code == 200 and verified.json()["status"] == "verified"
    assert _row(db_session, lesson_id).verified_by == _api_admin_user


def test_an_admin_lists_filters_edits_and_retires(client, db_session, login_as, _api_admin_user):
    login_as(_api_admin_user)
    first = client.post(CURATOR, json=_body(text="first")).json()["id"]
    second = client.post(CURATOR, json=_body(text="second")).json()["id"]
    client.post(f"{ADMIN}/{first}/verify")

    everything = client.get(ADMIN).json()
    assert everything["total"] == 2 and [i["id"] for i in everything["items"]] == [second, first]
    only = client.get(ADMIN, params={"status": "verified"}).json()
    assert [i["id"] for i in only["items"]] == [first] and only["items"][0]["created_by_username"]

    edited = client.patch(f"{ADMIN}/{second}", json={"text": "second, reworded", "category": None})
    assert edited.status_code == 200 and edited.json()["text"] == "second, reworded"
    assert edited.json()["category"] is None
    assert client.post(f"{ADMIN}/{second}/retire").json()["status"] == "retired"
    assert client.patch(f"{ADMIN}/{second}", json={"text": "late"}).status_code == 409
    assert _row(db_session, second).text == "second, reworded"


def test_text_of_501_characters_is_refused_and_500_is_accepted(client, db_session, login_as, _api_curator_user):
    login_as(_api_curator_user)
    assert client.post(CURATOR, json=_body(text="x" * 501)).status_code == 422
    assert _count(db_session) == 0
    assert client.post(CURATOR, json=_body(text="x" * 500)).status_code == 201
    assert _count(db_session) == 1


def test_unknown_ids_are_coded_404s_with_no_id_in_the_detail(client, db_session, login_as, _api_admin_user):
    login_as(_api_admin_user)
    missing = client.post(f"{ADMIN}/918273645/verify")
    assert missing.status_code == 404 and missing.json()["code"] == "machine_review_lesson_not_found"
    assert "918273645" not in missing.text
    unknown_job = client.post(CURATOR, json=_body(source_job_id=918273645))
    assert unknown_job.status_code == 404 and unknown_job.json()["code"] == "machine_review_job_not_found"
    assert "918273645" not in unknown_job.text
    assert _count(db_session) == 0


def test_a_retired_lesson_cannot_be_verified_again(client, db_session, login_as, _api_admin_user):
    login_as(_api_admin_user)
    lesson_id = client.post(CURATOR, json=_body()).json()["id"]
    client.post(f"{ADMIN}/{lesson_id}/retire")
    refused = client.post(f"{ADMIN}/{lesson_id}/verify")
    assert refused.status_code == 409 and refused.json()["code"] == "machine_review_lesson_retired"
    assert _row(db_session, lesson_id).status is S.retired


@pytest.mark.parametrize("extra", [{"id": 5}, {"status": "verified"}, {"created_by": 1}])
def test_the_request_refuses_fields_the_server_owns(client, login_as, _api_curator_user, extra):
    login_as(_api_curator_user)
    assert client.post(CURATOR, json=_body(**extra)).status_code == 422
    assert client.post(CURATOR, json=_body()).status_code == 201


def test_resolve_lesson_text_is_stripped_before_the_cap_like_the_create_route():
    from app.api.routes.admin import AdminCuratorTaskResolveRequest

    body = AdminCuratorTaskResolveRequest(
        resolution_state="dismissed_machine_finding", resolution_note="n", lesson_text="x" * 500 + " "
    )
    assert body.lesson_text == "x" * 500
