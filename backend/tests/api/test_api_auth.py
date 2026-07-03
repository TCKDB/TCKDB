"""Tests for the v1 auth/roles layer.

The session-scoped ``client`` fixture overrides ``get_current_user`` so
existing tests do not have to deal with session cookies or API keys.
These tests intentionally build a *fresh* ``TestClient`` (without that
override) so they exercise the real auth dependency.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_db, get_write_db
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.db.models.user_session import UserSession
from app.services.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_BY_ROLE,
)


@pytest.fixture
def raw_client(db_session) -> TestClient:
    """TestClient without the auth override, but sharing the txn-scoped session."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def _hydrogen_conformer_payload() -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": "conf-a",
        "note": "test upload",
    }


# ---------------------------------------------------------------------------
# 1. Session auth flow
# ---------------------------------------------------------------------------


class TestSessionAuthFlow:
    def test_register_login_me_logout(self, raw_client):
        resp = raw_client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "correct-horse", "email": "alice@example.com"},
        )
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["username"] == "alice"
        assert body["role"] == "user"
        # Registration sets the session cookie — /me should now work.
        assert SESSION_COOKIE_NAME in raw_client.cookies

        me_resp = raw_client.get("/api/v1/auth/me")
        assert me_resp.status_code == 200
        assert me_resp.json()["username"] == "alice"

        # Log out, cookie is cleared and /me requires auth again.
        logout_resp = raw_client.post("/api/v1/auth/logout")
        assert logout_resp.status_code == 204
        raw_client.cookies.clear()
        assert raw_client.get("/api/v1/auth/me").status_code == 401

        # Fresh login works.
        login_resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "correct-horse"},
        )
        assert login_resp.status_code == 200
        assert raw_client.get("/api/v1/auth/me").json()["username"] == "alice"

    def test_login_wrong_password_rejected(self, raw_client):
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "bob", "password": "right-password-1"},
        )
        raw_client.cookies.clear()
        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "wrong-password-2"},
        )
        assert resp.status_code == 401

    def test_anonymous_me_rejected(self, raw_client):
        assert raw_client.get("/api/v1/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# 2. API key creation requires session auth
# ---------------------------------------------------------------------------


class TestApiKeyCreation:
    def test_requires_session(self, raw_client):
        """Anonymous callers cannot mint keys."""
        resp = raw_client.post("/api/v1/auth/api-keys", json={"label": "arc"})
        assert resp.status_code == 401

    def test_logged_in_user_can_create_and_list(self, raw_client, db_session):
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "carol", "password": "password-123"},
        )
        resp = raw_client.post("/api/v1/auth/api-keys", json={"label": "arc"})
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["key"].startswith("tck_")
        assert body["label"] == "arc"
        key_id = body["id"]

        # Plain key only returned once — list response omits it.
        list_resp = raw_client.get("/api/v1/auth/api-keys")
        assert list_resp.status_code == 200
        assert all("key" not in item for item in list_resp.json())
        assert any(item["id"] == key_id for item in list_resp.json())

    def test_api_key_bearer_cannot_mint_more_keys(self, raw_client, db_session):
        """The key-issuing surface is session-only on purpose."""
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "dave", "password": "password-123"},
        )
        key = raw_client.post("/api/v1/auth/api-keys", json={}).json()["key"]
        raw_client.cookies.clear()

        resp = raw_client.post(
            "/api/v1/auth/api-keys",
            json={"label": "no"},
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3–5. Upload auth via API key + attribution
# ---------------------------------------------------------------------------


class TestUploadWithApiKey:
    def test_anonymous_upload_rejected(self, raw_client):
        resp = raw_client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        )
        assert resp.status_code == 401

    def test_valid_api_key_authenticates_and_attributes(
        self, raw_client, db_session
    ):
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "erin", "password": "password-123"},
        )
        key = raw_client.post("/api/v1/auth/api-keys", json={}).json()["key"]
        raw_client.cookies.clear()

        user_id = db_session.scalar(
            select(AppUser.id).where(AppUser.username == "erin")
        )

        resp = raw_client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(),
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 201, resp.json()
        observation_id = resp.json()["id"]

        # Attribution: the observation's created_by row id == key-owner id.
        from app.db.models.species import ConformerObservation

        obs = db_session.get(ConformerObservation, observation_id)
        assert obs is not None
        assert obs.created_by == user_id

    def test_invalid_key_rejected(self, raw_client):
        resp = raw_client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(),
            headers={"X-API-Key": "tck_not-a-real-key"},
        )
        assert resp.status_code == 401

    def test_revoked_key_rejected(self, raw_client, db_session):
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "frank", "password": "password-123"},
        )
        create_resp = raw_client.post("/api/v1/auth/api-keys", json={})
        key = create_resp.json()["key"]
        key_id = create_resp.json()["id"]

        del_resp = raw_client.delete(f"/api/v1/auth/api-keys/{key_id}")
        assert del_resp.status_code == 204
        raw_client.cookies.clear()

        resp = raw_client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(),
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 6. Role enforcement
# ---------------------------------------------------------------------------


class TestRoleChangeEndpoint:
    def _promote_to_admin(self, db_session, username: str) -> None:
        user = db_session.scalar(select(AppUser).where(AppUser.username == username))
        user.role = AppUserRole.admin
        db_session.flush()

    def test_non_admin_cannot_change_roles(self, raw_client, db_session):
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "gina", "password": "password-123"},
        )
        # Target user
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "harry", "password": "password-123"},
        )
        target_id = db_session.scalar(
            select(AppUser.id).where(AppUser.username == "harry")
        )
        # Re-login as gina (non-admin)
        raw_client.cookies.clear()
        raw_client.post(
            "/api/v1/auth/login",
            json={"username": "gina", "password": "password-123"},
        )
        resp = raw_client.patch(
            f"/api/v1/admin/users/{target_id}/role",
            json={"role": "curator"},
        )
        assert resp.status_code == 403

    def test_admin_can_change_roles(self, raw_client, db_session):
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "ivy", "password": "password-123"},
        )
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "jack", "password": "password-123"},
        )
        target_id = db_session.scalar(
            select(AppUser.id).where(AppUser.username == "jack")
        )
        self._promote_to_admin(db_session, "ivy")
        raw_client.cookies.clear()
        raw_client.post(
            "/api/v1/auth/login",
            json={"username": "ivy", "password": "password-123"},
        )

        resp = raw_client.patch(
            f"/api/v1/admin/users/{target_id}/role",
            json={"role": "curator"},
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["role"] == "curator"

        db_session.expire_all()
        updated = db_session.get(AppUser, target_id)
        assert updated.role is AppUserRole.curator

    def test_anonymous_role_change_rejected(self, raw_client, db_session):
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "kate", "password": "password-123"},
        )
        target_id = db_session.scalar(
            select(AppUser.id).where(AppUser.username == "kate")
        )
        raw_client.cookies.clear()
        resp = raw_client.patch(
            f"/api/v1/admin/users/{target_id}/role",
            json={"role": "admin"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 7. Role-based session TTL + fixed-expiry policy
# ---------------------------------------------------------------------------


def _latest_session(db_session, username: str) -> UserSession:
    user_id = db_session.scalar(select(AppUser.id).where(AppUser.username == username))
    row = db_session.scalar(
        select(UserSession)
        .where(UserSession.user_id == user_id)
        .order_by(UserSession.id.desc())
    )
    assert row is not None, "expected a session row for this user"
    return row


class TestRoleBasedSessionTtl:
    """Each role gets a different fixed TTL; resolving a session never bumps it."""

    @pytest.mark.parametrize(
        "role, expected",
        [
            (AppUserRole.user, timedelta(days=7)),
            (AppUserRole.curator, timedelta(days=3)),
            (AppUserRole.admin, timedelta(hours=12)),
        ],
    )
    def test_login_ttl_matches_role(self, raw_client, db_session, role, expected):
        username = f"ttl-{role.value}"
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "password-123"},
        )
        # Promote to the role under test (registration always lands as ``user``).
        user = db_session.scalar(select(AppUser).where(AppUser.username == username))
        user.role = role
        db_session.flush()
        raw_client.cookies.clear()

        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "password-123"},
        )
        assert resp.status_code == 200, resp.json()

        row = _latest_session(db_session, username)
        actual = row.expires_at - row.created_at
        # Allow a generous DB-clock tolerance: this is a fixed-window assertion,
        # not a precision benchmark.
        tolerance = timedelta(seconds=5)
        assert abs(actual - expected) <= tolerance, (
            f"role={role.value}: expected ~{expected}, got {actual}"
        )
        assert SESSION_TTL_BY_ROLE[role] == expected

    def test_resolve_session_does_not_extend_expiry(self, raw_client, db_session):
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "no-bump", "password": "password-123"},
        )
        before = _latest_session(db_session, "no-bump").expires_at

        # Two authenticated round-trips that go through ``resolve_session``.
        assert raw_client.get("/api/v1/auth/me").status_code == 200
        assert raw_client.get("/api/v1/auth/me").status_code == 200

        db_session.expire_all()
        after = _latest_session(db_session, "no-bump").expires_at
        assert before == after, "expires_at must not change on activity"

    def test_logout_revokes_session_row(self, raw_client, db_session):
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "rev", "password": "password-123"},
        )
        row = _latest_session(db_session, "rev")
        assert row.revoked_at is None

        assert raw_client.post("/api/v1/auth/logout").status_code == 204

        db_session.expire_all()
        row = _latest_session(db_session, "rev")
        assert row.revoked_at is not None


# ---------------------------------------------------------------------------
# 8. Registration policy gate
# ---------------------------------------------------------------------------


class TestRegistrationPolicy:
    """``AUTH_ALLOW_OPEN_REGISTRATION`` toggles public registration on/off."""

    def test_registration_allowed_when_enabled(self, raw_client):
        # Default for local/dev: registration is open.
        assert settings.auth_allow_open_registration is True
        resp = raw_client.post(
            "/api/v1/auth/register",
            json={"username": "open-reg", "password": "password-123"},
        )
        assert resp.status_code == 201, resp.json()

    def test_registration_rejected_when_disabled(self, raw_client, monkeypatch):
        monkeypatch.setattr(settings, "auth_allow_open_registration", False)
        resp = raw_client.post(
            "/api/v1/auth/register",
            json={"username": "closed-reg", "password": "password-123"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body.get("detail") == (
            "Public registration is disabled on this deployment."
        )

    def test_login_still_works_when_registration_disabled(
        self, raw_client, db_session, monkeypatch
    ):
        # Pre-existing user (e.g. admin-seeded) can still log in even when
        # public registration is off.
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "seeded", "password": "password-123"},
        )
        raw_client.cookies.clear()
        monkeypatch.setattr(settings, "auth_allow_open_registration", False)
        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "seeded", "password": "password-123"},
        )
        assert resp.status_code == 200
