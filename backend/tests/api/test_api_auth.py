"""Tests for the v1 auth/roles layer.

The session-scoped ``client`` fixture overrides ``get_current_user`` so
existing tests do not have to deal with session cookies or API keys.
These tests intentionally build a *fresh* ``TestClient`` (without that
override) so they exercise the real auth dependency.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.app import create_app
from app.api.code_catalogue import CATALOGUE
from app.api.config import settings
from app.api.deps import get_db, get_write_db
from app.api.error_contract import _CODE_POSITION_PATTERN as _CODE_POSITION
from app.api.routes import auth as auth_routes
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.db.models.user_session import UserSession
from app.services.auth import (
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE_NAME,
    SESSION_TTL_BY_ROLE,
    password_needs_rehash,
    verify_password,
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


# ---------------------------------------------------------------------------
# 9. Opportunistic password re-hashing on login
# ---------------------------------------------------------------------------


def _legacy_pbkdf2_hash(plain: str) -> str:
    """The pre-scrypt stored format, rebuilt without ``app.services.auth``.

    Deployed accounts carry hashes in exactly this shape. Building it
    from :mod:`hashlib` here keeps the test honest even after the app
    stops emitting the format.
    """
    salt = bytes.fromhex("0f1e2d3c4b5a69788796a5b4c3d2e1f0")
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


class TestPasswordRehashOnLogin:
    """Login is the only moment the plaintext exists, so it is the only
    moment a legacy hash can be upgraded in place."""

    PASSWORD = "legacy-user-password"

    def _seed_legacy_user(self, db_session, username: str) -> int:
        user = AppUser(
            username=username,
            password_hash=_legacy_pbkdf2_hash(self.PASSWORD),
            role=AppUserRole.user,
            is_active=True,
        )
        db_session.add(user)
        db_session.flush()
        return user.id

    def _persisted_hash(self, db_session, user_id: int) -> str:
        db_session.expire_all()
        return db_session.scalar(
            select(AppUser.password_hash).where(AppUser.id == user_id)
        )

    def test_legacy_hash_is_replaced_on_successful_login(
        self, raw_client, db_session
    ):
        user_id = self._seed_legacy_user(db_session, "legacy-alice")
        before = self._persisted_hash(db_session, user_id)
        assert before.startswith("pbkdf2_sha256$")

        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "legacy-alice", "password": self.PASSWORD},
        )
        assert resp.status_code == 200, resp.json()

        after = self._persisted_hash(db_session, user_id)
        assert after != before
        assert after.startswith("scrypt$")
        assert verify_password(self.PASSWORD, after)

        # And the upgraded hash is usable: same password, fresh login.
        raw_client.cookies.clear()
        again = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "legacy-alice", "password": self.PASSWORD},
        )
        assert again.status_code == 200, again.json()
        # Second login finds nothing to migrate, so the hash stays put.
        assert self._persisted_hash(db_session, user_id) == after

    def test_failed_login_does_not_rehash(self, raw_client, db_session):
        user_id = self._seed_legacy_user(db_session, "legacy-bob")
        before = self._persisted_hash(db_session, user_id)

        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "legacy-bob", "password": "wrong-password"},
        )
        assert resp.status_code == 401
        assert self._persisted_hash(db_session, user_id) == before

    def test_rehash_failure_does_not_fail_the_login(
        self, raw_client, db_session, monkeypatch
    ):
        """The user authenticated correctly; a storage problem is ours."""
        user_id = self._seed_legacy_user(db_session, "legacy-carol")
        before = self._persisted_hash(db_session, user_id)

        def _boom(_plain: str) -> str:
            raise RuntimeError("hashing backend unavailable")

        monkeypatch.setattr(auth_routes, "hash_password", _boom)

        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "legacy-carol", "password": self.PASSWORD},
        )
        assert resp.status_code == 200, resp.json()
        # Old hash survives untouched, so the next login can retry.
        assert self._persisted_hash(db_session, user_id) == before

        # The session really was issued, not just a 200 shell.
        assert raw_client.get("/api/v1/auth/me").json()["username"] == "legacy-carol"

    def test_inactive_legacy_user_is_not_rehashed(self, raw_client, db_session):
        user_id = self._seed_legacy_user(db_session, "legacy-dan")
        db_session.get(AppUser, user_id).is_active = False
        db_session.flush()
        before = self._persisted_hash(db_session, user_id)

        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "legacy-dan", "password": self.PASSWORD},
        )
        assert resp.status_code == 403
        assert self._persisted_hash(db_session, user_id) == before


# ---------------------------------------------------------------------------
# 10. Login does not leak which usernames exist
# ---------------------------------------------------------------------------


class TestLoginAccountEnumeration:
    """An unknown username must cost the same as a wrong password.

    These assert the *mechanism* — that the KDF runs on both paths —
    rather than measuring elapsed time. A stopwatch assertion would
    flake on a loaded CI box and end up skipped, which is worse than no
    test at all; "verify_password was called exactly once" is the
    property that actually matters and it cannot flake.
    """

    @pytest.fixture
    def verify_calls(self, monkeypatch) -> list:
        """Record every ``verify_password`` call the login route makes."""
        calls: list = []
        real = auth_routes.verify_password

        def _spy(plain, stored):
            calls.append(stored)
            return real(plain, stored)

        monkeypatch.setattr(auth_routes, "verify_password", _spy)
        return calls

    def _register(self, raw_client) -> None:
        raw_client.post(
            "/api/v1/auth/register",
            json={"username": "known-user", "password": "password-123"},
        )
        raw_client.cookies.clear()

    def test_unknown_username_still_runs_the_kdf(self, raw_client, verify_calls):
        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "no-such-user", "password": "whatever-123"},
        )
        assert resp.status_code == 401
        assert len(verify_calls) == 1, (
            "unknown username must still verify against a decoy; skipping the "
            "KDF turns response latency into a username oracle"
        )
        assert verify_calls[0] == DUMMY_PASSWORD_HASH

    def test_wrong_password_runs_the_kdf_exactly_once_too(
        self, raw_client, verify_calls
    ):
        self._register(raw_client)
        verify_calls.clear()  # registration does not verify, but be explicit
        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "known-user", "password": "wrong-password"},
        )
        assert resp.status_code == 401
        assert len(verify_calls) == 1
        assert verify_calls[0] != DUMMY_PASSWORD_HASH

    def test_decoy_tracks_the_current_parameters(self):
        """The decoy must cost what a real verification costs.

        Comparing against ``password_needs_rehash`` means a future
        ``_SCRYPT_N`` bump cannot leave the decoy cheaper than a real
        hash — which would quietly reopen the gap in the other
        direction.
        """
        assert DUMMY_PASSWORD_HASH.startswith("scrypt$")
        assert password_needs_rehash(DUMMY_PASSWORD_HASH) is False

    def test_decoy_is_not_a_usable_credential(self, raw_client, db_session):
        """Nobody can log in as a user that does not exist."""
        for attempt in ("", "password", "whatever-123", DUMMY_PASSWORD_HASH):
            resp = raw_client.post(
                "/api/v1/auth/login",
                json={"username": "no-such-user", "password": attempt or "x"},
            )
            assert resp.status_code == 401
        assert verify_password("password", DUMMY_PASSWORD_HASH) is False

    def test_user_with_no_password_set_still_runs_the_kdf(
        self, raw_client, db_session, verify_calls
    ):
        """Archive restore leaves ``password_hash`` NULL — same rule applies."""
        db_session.add(
            AppUser(
                username="no-password",
                password_hash=None,
                role=AppUserRole.user,
                is_active=True,
            )
        )
        db_session.flush()

        resp = raw_client.post(
            "/api/v1/auth/login",
            json={"username": "no-password", "password": "anything-123"},
        )
        assert resp.status_code == 401
        assert len(verify_calls) == 1
        # `verify_password` burns an equivalent hash internally for this case.
        assert verify_calls[0] is None


# ---------------------------------------------------------------------------
# 10. Registration conflicts name the field they refused
# ---------------------------------------------------------------------------


class TestRegistrationConflictCodes:
    """Which field was taken, as a code, not as an English sentence.

    A taken username and a taken email address both arrive as SQLSTATE
    23505, so nothing derived from the SQLSTATE can tell them apart --
    ``unique_conflict`` would be a code that says less than the prose it
    replaced. The route reads the constraint name out of psycopg's
    structured diagnostics instead, and these tests hold it to reporting
    a *different* code for each, because a handler answering one code for
    both would pass any test asserting only that a 409 arrived.

    Each conflict gets its own test function. Registration's rollback on
    conflict unwinds the transaction the fixture holds open, which takes
    the first account with it -- so provoking both conflicts inside one
    test makes the second one register successfully and prove nothing.
    That is not a quirk of the route; in production every request has its
    own session. It is a property of the shared txn-scoped fixture, and
    it silently turned a two-conflict reproduction into a 201.
    """

    def test_a_free_username_and_email_are_accepted(self, raw_client):
        """The accepted case, so the codes below are not vacuous.

        A route that 409'd on every registration would satisfy both
        conflict tests. This is what says the 409s are refusals of
        something specific rather than the endpoint's only behaviour.
        """
        resp = raw_client.post(
            "/api/v1/auth/register",
            json={
                "username": "novel-name",
                "email": "novel-name@example.org",
                "password": "password-123",
            },
        )
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["username"] == "novel-name"
        assert "code" not in body

    def test_a_taken_username_reports_username_taken(self, raw_client):
        first = raw_client.post(
            "/api/v1/auth/register",
            json={
                "username": "taken-name",
                "email": "first@example.org",
                "password": "password-123",
            },
        )
        assert first.status_code == 201, first.json()
        raw_client.cookies.clear()

        resp = raw_client.post(
            "/api/v1/auth/register",
            json={
                "username": "taken-name",
                # A free address, so only the username can be the cause.
                "email": "second@example.org",
                "password": "password-123",
            },
        )
        assert resp.status_code == 409, resp.json()
        body = resp.json()
        assert body["code"] == "username_taken", body
        assert body["code"] != "email_taken"
        # The sentence stays useful to a human reading a traceback, and
        # now says which field -- the old one could not. The ``"code: "``
        # prefix stays in ``detail`` as well; that is the convention
        # every other message_prefix refusal follows, not an oversight.
        assert "username" in body["detail"].lower()
        assert body["detail"] != auth_routes.REGISTRATION_CONFLICT_FALLBACK
        # No row id reaches the client (DR-0028 Requirement 2), and there
        # is nothing structured to carry here beyond the code.
        assert body["context"] == {}

    def test_a_taken_email_reports_email_taken(self, raw_client):
        first = raw_client.post(
            "/api/v1/auth/register",
            json={
                "username": "first-name",
                "email": "taken@example.org",
                "password": "password-123",
            },
        )
        assert first.status_code == 201, first.json()
        raw_client.cookies.clear()

        resp = raw_client.post(
            "/api/v1/auth/register",
            json={
                # A free username, so only the address can be the cause.
                "username": "second-name",
                "email": "taken@example.org",
                "password": "password-123",
            },
        )
        assert resp.status_code == 409, resp.json()
        body = resp.json()
        assert body["code"] == "email_taken", body
        assert body["code"] != "username_taken"
        assert "email" in body["detail"].lower()
        assert body["detail"] != auth_routes.REGISTRATION_CONFLICT_FALLBACK
        assert body["context"] == {}

    def test_an_omitted_email_is_not_a_conflict(self, raw_client):
        """NULL is not equal to NULL, so address-less accounts coexist.

        Worth pinning: ``email`` is nullable *and* unique, and a reader
        who sees ``UniqueConstraint("email")`` may reasonably expect the
        second address-less registration to be refused. PostgreSQL's
        default ``NULLS DISTINCT`` is what makes registration without an
        address usable at all, and a migration adding ``NULLS NOT
        DISTINCT`` would take that away silently.
        """
        first = raw_client.post(
            "/api/v1/auth/register",
            json={"username": "no-address-one", "password": "password-123"},
        )
        assert first.status_code == 201, first.json()
        raw_client.cookies.clear()

        second = raw_client.post(
            "/api/v1/auth/register",
            json={"username": "no-address-two", "password": "password-123"},
        )
        assert second.status_code == 201, second.json()

    def test_an_unmapped_constraint_falls_back_without_naming_a_field(self):
        """The fallback must not guess, and must not leak the constraint.

        Exercised against the classifier directly: provoking an unmapped
        ``app_user`` uniqueness rule through the wire would mean adding
        one to the schema, and the point of the branch is what it does
        about a rule that is *not* in the model today -- one a future
        migration adds and nobody remembers to classify.
        """

        class _Diag:
            constraint_name = "uq_app_user_something_new"

        class _Orig:
            diag = _Diag()
            sqlstate = "23505"

        exc = IntegrityError("stmt", {}, Exception())
        exc.orig = _Orig()  # type: ignore[assignment]

        detail = auth_routes._registration_conflict_detail(exc)
        assert detail == auth_routes.REGISTRATION_CONFLICT_FALLBACK
        assert "uq_app_user_something_new" not in detail
        # Nothing in the code position, so the envelope reports http_409
        # rather than inventing a code for a rule nobody classified.
        assert not _CODE_POSITION.match(detail)

    def test_a_driver_reporting_no_constraint_falls_back(self):
        """``diag`` is absent on some wrapped errors; that must not raise."""
        exc = IntegrityError("stmt", {}, Exception())
        assert (
            auth_routes._registration_conflict_detail(exc)
            == auth_routes.REGISTRATION_CONFLICT_FALLBACK
        )

    def test_both_mapped_details_declare_their_code(self):
        """Each mapping value must actually carry a code in the code position.

        The promotion is by convention -- a ``"code: message"`` prefix --
        so a value edited into plain prose would keep the 409, lose the
        code, and break no other assertion in this file that a wire test
        happens not to cover.

        The codes are also required to be *distinct*, which is the whole
        premise: two constraints answering one code is exactly the
        under-specified ``unique_conflict`` this replaced, wearing a more
        specific name.
        """
        codes = {}
        for constraint, detail in auth_routes.REGISTRATION_CONFLICTS.items():
            match = _CODE_POSITION.match(detail)
            assert match is not None, (constraint, detail)
            codes[constraint] = match.group(1)

        assert codes == {
            "uq_app_user_username": "username_taken",
            "uq_app_user_email": "email_taken",
        }, codes
        assert len(set(codes.values())) == len(codes), codes

    def test_the_two_codes_are_catalogued_at_the_status_they_arrive_at(self):
        """A code a client cannot import is a code it cannot branch on.

        The catalogue gates the generated ``RejectionCode`` enum, and an
        entry filed at the wrong status would give a caller the wrong
        retry advice -- 422 says "resend a corrected payload", which is
        false of a username somebody else holds.
        """
        entries = {
            entry.code: entry
            for entry in CATALOGUE
            if entry.code in {"username_taken", "email_taken"}
        }
        assert set(entries) == {"username_taken", "email_taken"}
        for entry in entries.values():
            assert entry.status == 409, entry
            assert entry.is_client_facing, entry
