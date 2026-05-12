"""Tests for the login/register throttle.

The rate-limit middleware uses dedicated low-budget buckets for the
auth surface — login is per-minute, register is per-hour. We dial
both budgets down to 2 in the fixture so the throttle fires after a
small handful of attempts.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_db, get_write_db
from app.api.rate_limit import reset_rate_limit_store


@pytest.fixture
def auth_throttled_client(db_session: Session, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_login_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_register_per_hour", 2)
    monkeypatch.setattr(settings, "auth_allow_open_registration", True)
    reset_rate_limit_store()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    reset_rate_limit_store()


def test_repeated_failed_logins_return_429(auth_throttled_client):
    payload = {"username": "nobody", "password": "wrong"}
    # The first N attempts fail with 401 (invalid credentials).
    for _ in range(settings.rate_limit_auth_login_per_minute):
        r = auth_throttled_client.post("/api/v1/auth/login", json=payload)
        assert r.status_code == 401
    # The next attempt is rejected by the rate limiter.
    blocked = auth_throttled_client.post("/api/v1/auth/login", json=payload)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["code"] == "rate_limit_exceeded"
    assert body["bucket"] == "login"


def test_login_error_does_not_reveal_account_existence(
    auth_throttled_client, db_session
):
    """401 detail for an unknown user must match the detail for a wrong password."""
    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole
    from app.services.auth import hash_password

    db_session.add(
        AppUser(
            username="alice",
            password_hash=hash_password("secret123"),
            role=AppUserRole.user,
            is_active=True,
        )
    )
    db_session.flush()

    no_such = auth_throttled_client.post(
        "/api/v1/auth/login", json={"username": "no_such", "password": "x"}
    )
    bad_pw = auth_throttled_client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "wrong"}
    )
    assert no_such.status_code == 401
    assert bad_pw.status_code == 401
    assert no_such.json()["detail"] == bad_pw.json()["detail"]


def test_register_returns_429_after_budget(auth_throttled_client):
    base = {"password": "longenough", "email": None}
    for i in range(settings.rate_limit_register_per_hour):
        r = auth_throttled_client.post(
            "/api/v1/auth/register",
            json={**base, "username": f"u{i}"},
        )
        # 201 created or 409 conflict — either is "the handler ran".
        assert r.status_code != 429
    blocked = auth_throttled_client.post(
        "/api/v1/auth/register",
        json={**base, "username": "u_overflow"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limit_exceeded"
    assert blocked.json()["bucket"] == "register"
