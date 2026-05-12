"""Tests for the session cookie security flags (F16)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_db, get_write_db


@pytest.fixture
def raw_client(db_session: Session) -> TestClient:
    """TestClient without the auth override so /auth/login actually runs."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    return TestClient(app)


def _seed_user(db_session: Session, *, username: str, password: str) -> None:
    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole
    from app.services.auth import hash_password

    db_session.add(
        AppUser(
            username=username,
            password_hash=hash_password(password),
            role=AppUserRole.user,
            is_active=True,
        )
    )
    db_session.flush()


def test_secure_flag_set_when_enabled(raw_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    _seed_user(db_session, username="cookieuser", password="testpass1")

    r = raw_client.post(
        "/api/v1/auth/login",
        json={"username": "cookieuser", "password": "testpass1"},
    )
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie
    # SameSite default for the project is ``lax``.
    assert "samesite=lax" in set_cookie.lower()


def test_secure_flag_omitted_when_disabled(raw_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    _seed_user(db_session, username="cookieuser2", password="testpass1")

    r = raw_client.post(
        "/api/v1/auth/login",
        json={"username": "cookieuser2", "password": "testpass1"},
    )
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    # Local-dev cookies should not carry Secure (login over plain HTTP).
    assert "Secure" not in set_cookie
    # HttpOnly stays on regardless.
    assert "HttpOnly" in set_cookie


def test_samesite_setting_is_honored(raw_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_samesite", "strict")
    _seed_user(db_session, username="cookieuser3", password="testpass1")

    r = raw_client.post(
        "/api/v1/auth/login",
        json={"username": "cookieuser3", "password": "testpass1"},
    )
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "samesite=strict" in set_cookie.lower()
