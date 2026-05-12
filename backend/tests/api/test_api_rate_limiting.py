"""Tests for the application-level rate limiter.

The autouse fixture in ``conftest.py`` disables the limiter for every
test; tests in this module flip it back on via the
``rate_limited_client`` fixture so each scenario can assert against
the fixed-window behavior in isolation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_current_user, get_db, get_write_db
from app.api.rate_limit import reset_rate_limit_store
from app.db.models.app_user import AppUser


@pytest.fixture
def rate_limited_client(db_session: Session, monkeypatch):
    """TestClient that exercises the real RateLimitMiddleware.

    Builds a fresh app — the rate-limit settings are read at request
    time so the monkeypatch on ``settings`` is enough. No auth
    override is installed so the test traffic is treated as
    anonymous.
    """
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_anon_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 5)
    monkeypatch.setattr(settings, "rate_limit_auth_login_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_register_per_hour", 2)
    reset_rate_limit_store()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    reset_rate_limit_store()


def test_anonymous_reads_are_rate_limited(rate_limited_client):
    """After ``rate_limit_anon_per_minute`` hits, the next request returns 429."""
    # Use the health endpoint... no, health is bypassed. Use a real route.
    path = "/api/v1/scientific/reactions/search"
    # First N requests within budget succeed (they may return 422 for
    # missing filters — but the rate limit fires before the handler).
    for _ in range(settings.rate_limit_anon_per_minute):
        r = rate_limited_client.get(path)
        assert r.status_code != 429

    blocked = rate_limited_client.get(path)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["code"] == "rate_limit_exceeded"
    assert body["bucket"] == "anon"
    assert body["retry_after_seconds"] >= 1
    assert blocked.headers.get("retry-after") is not None


def test_health_endpoint_is_not_rate_limited(rate_limited_client):
    """Operators ping /health on a loop; the limiter must let those pass."""
    for _ in range(settings.rate_limit_anon_per_minute + 5):
        r = rate_limited_client.get("/api/v1/health")
        assert r.status_code == 200


def test_auth_credential_uses_separate_bucket(rate_limited_client, db_session):
    """A request bearing an X-API-Key header hits the larger auth bucket.

    We don't need a *valid* key for the limiter — it only fingerprints
    the header for bucket selection. The handler may still return 401
    for the bogus credential, but the response is *not* 429.
    """
    anon_path = "/api/v1/scientific/reactions/search"
    # Drain the anonymous bucket.
    for _ in range(settings.rate_limit_anon_per_minute):
        rate_limited_client.get(anon_path)
    blocked = rate_limited_client.get(anon_path)
    assert blocked.status_code == 429

    # Same path with an API-key header lands in the auth bucket.
    for _ in range(settings.rate_limit_auth_per_minute):
        r = rate_limited_client.get(anon_path, headers={"X-API-Key": "bogus"})
        assert r.status_code != 429
    over = rate_limited_client.get(anon_path, headers={"X-API-Key": "bogus"})
    assert over.status_code == 429
    assert over.json()["bucket"] == "auth"


def test_rate_limit_can_be_disabled(rate_limited_client, monkeypatch):
    """Flipping the master switch off short-circuits the middleware."""
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    reset_rate_limit_store()
    path = "/api/v1/scientific/reactions/search"
    for _ in range(10):  # well over the budget
        r = rate_limited_client.get(path)
        assert r.status_code != 429
