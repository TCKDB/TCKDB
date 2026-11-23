"""``GET /machine-review/providers`` and the admin switch routes, and their agreement with ``POST /jobs``."""

# ruff: noqa: F811
from __future__ import annotations

import itertools

import pytest

from app.api.config import settings as app_settings
from app.db.models.common import MachineReviewProviderKey
from tests.api._machine_review_job_routes_support import (  # noqa: F401
    JOBS,
    PROVIDERS,
    SETTINGS,
    anon_client,
    fake_mode,
    job_count,
    submission,
)


def _providers(client):
    response = client.get(PROVIDERS)
    assert response.status_code == 200, response.text
    return {p["provider_key"]: p["availability"] for p in response.json()["providers"]}


def _patch(client, **body):
    return client.patch(SETTINGS, json=body)


def _post(client, submission, key="fake"):
    return client.post(JOBS, json={"submission_id": submission.id, "provider_key": key})


@pytest.mark.usefixtures("fake_mode")
def test_a_curator_sees_remote_keys_refused_and_fake_ok(client, login_as, _api_curator_user):
    login_as(_api_curator_user)
    listed = _providers(client)
    assert set(listed) == {k.value for k in MachineReviewProviderKey}
    assert listed["claude_code"] == listed["codex"] == "not_allowed_for_user"
    assert listed["fake"] == "ok"


@pytest.mark.usefixtures("fake_mode")
def test_curator_switch_blocks_a_curator_with_403_and_not_an_admin(
    client, db_session, login_as, _api_curator_user, _api_admin_user, submission
):
    login_as(_api_admin_user)
    assert _patch(client, curator_requests_enabled=False).status_code == 200
    login_as(_api_curator_user)
    refused = _post(client, submission)
    assert refused.status_code == 403
    assert refused.json()["code"] == "machine_review_curator_requests_disabled"
    assert job_count(db_session) == 0
    assert set(_providers(client).values()) == {"curator_requests_disabled"}
    login_as(_api_admin_user)
    assert _post(client, submission).status_code == 202


@pytest.mark.usefixtures("fake_mode")
def test_review_switch_and_mode_off_refuse_with_409(
    client, db_session, login_as, _api_admin_user, submission, monkeypatch
):
    login_as(_api_admin_user)
    assert _patch(client, machine_review_enabled=False).status_code == 200
    refused = _post(client, submission)
    assert (refused.status_code, refused.json()["code"]) == (409, "machine_review_disabled")
    assert set(_providers(client).values()) == {"machine_review_disabled"}
    _patch(client, machine_review_enabled=True)

    monkeypatch.setattr(app_settings, "ai_review_assistant_mode", "off")
    off = _post(client, submission)
    assert (off.status_code, off.json()["code"]) == (409, "machine_review_mode_off")
    assert set(_providers(client).values()) == {"mode_off"}
    assert job_count(db_session) == 0


_ROLES = ["admin", "curator"]
_SWITCHES = list(itertools.product([True, False], repeat=2))
_MODES = ["test", "off", "cloud"]


@pytest.mark.parametrize("role", _ROLES)
@pytest.mark.parametrize("switches", _SWITCHES)
@pytest.mark.parametrize("mode", _MODES)
def test_providers_say_ok_exactly_when_the_request_is_accepted(
    client, db_session, login_as, _api_curator_user, _api_admin_user, submission, monkeypatch, role, switches, mode
):
    monkeypatch.setattr(app_settings, "ai_review_assistant_mode", mode)
    monkeypatch.setattr(app_settings, "deployment_mode", "local")
    login_as(_api_admin_user)
    if mode != "off":
        assert _patch(
            client, machine_review_enabled=switches[0], curator_requests_enabled=switches[1]
        ).status_code == 200
    login_as(_api_curator_user if role == "curator" else _api_admin_user)
    listed = _providers(client)
    for key in MachineReviewProviderKey:
        response = _post(client, submission, key.value)
        assert (listed[key.value] == "ok") == (response.status_code == 202), (key, listed, response.text)
        if response.status_code == 202:
            client.post(f"{JOBS}/{response.json()['id']}/cancel")


def test_settings_routes_are_admin_only(client, anon_client, login_as, _api_curator_user):
    assert client.get(SETTINGS).status_code == 403
    login_as(_api_curator_user)
    assert client.get(SETTINGS).status_code == 403
    assert _patch(client, machine_review_enabled=False).status_code == 403
    assert anon_client.get(SETTINGS).status_code == 401


@pytest.mark.usefixtures("fake_mode")
def test_admin_patch_changes_only_the_fields_it_sends(client, login_as, _api_admin_user):
    login_as(_api_admin_user)
    start = client.get(SETTINGS).json()
    assert (start["machine_review_enabled"], start["curator_requests_enabled"]) == (True, True)
    assert (start["assistant_mode"], start["review_allowed"], start["curator_allowed"]) == ("test", True, True)

    changed = _patch(client, curator_requests_enabled=False).json()
    assert (changed["machine_review_enabled"], changed["curator_requests_enabled"]) == (True, False)
    assert changed["curator_allowed"] is False and changed["review_allowed"] is True
    assert client.get(SETTINGS).json() == changed


def test_patch_needs_a_known_field(client, login_as, _api_admin_user):
    login_as(_api_admin_user)
    assert _patch(client).status_code == 422
    assert _patch(client, unknown=True).status_code == 422
