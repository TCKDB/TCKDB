"""Tests for the application-level rate limiter.

The autouse fixture in ``conftest.py`` disables the limiter for every
test; tests in this module flip it back on via the
``rate_limited_client`` fixture so each scenario can assert against
the fixed-window behavior in isolation.

Bucket coverage:

- ``login`` / ``register`` — unchanged from the original limiter.
- ``anon_read`` — anonymous public scientific reads (GET + POST search).
- ``auth_read`` — same routes with a credential present, plus the
  authenticated non-mutating fallback for non-public-read paths.
- ``auth_write`` — authenticated mutating routes (uploads, admin, etc.).
- ``anon_other`` — anonymous everything-else, including stray mutating
  POSTs that must not inherit the read budget.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_db, get_write_db
from app.api.rate_limit import reset_rate_limit_store


# A real authenticated mutating route; the upload handler will refuse
# the bogus credential well before parsing the body, so the limiter
# decision is observable independent of the request shape.
_AUTH_WRITE_PATH = "/api/v1/uploads/conformers"

# Anonymous mutating route that is not login/register and not a
# scientific search — must land in ``anon_other``, not ``anon_read``.
_ANON_MUTATING_PATH = "/api/v1/uploads/conformers"

# Public scientific read endpoints.
_SCIENTIFIC_GET_PATH = "/api/v1/scientific/reactions/search"
_SCIENTIFIC_POST_SEARCH_PATH = "/api/v1/scientific/reactions/search"


@pytest.fixture
def rate_limited_client(db_session: Session, monkeypatch):
    """TestClient that exercises the real RateLimitMiddleware.

    Builds a fresh app — the rate-limit settings are read at request
    time so the monkeypatch on ``settings`` is enough. No auth
    override is installed so the test traffic is treated as
    anonymous unless the test supplies an ``X-API-Key`` header.
    """
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_anon_read_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_auth_read_per_minute", 5)
    monkeypatch.setattr(settings, "rate_limit_auth_write_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_anon_other_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_auth_login_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_register_per_hour", 2)
    reset_rate_limit_store()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    reset_rate_limit_store()


# ---------------------------------------------------------------------------
# Bucket: anon_read
# ---------------------------------------------------------------------------


def test_anonymous_scientific_get_uses_anon_read(rate_limited_client):
    """GET on a scientific surface from an anonymous client → anon_read."""
    for _ in range(settings.rate_limit_anon_read_per_minute):
        r = rate_limited_client.get(_SCIENTIFIC_GET_PATH)
        assert r.status_code != 429

    blocked = rate_limited_client.get(_SCIENTIFIC_GET_PATH)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["code"] == "rate_limit_exceeded"
    assert body["bucket"] == "anon_read"
    assert body["retry_after_seconds"] >= 1
    assert blocked.headers.get("retry-after") is not None


def test_anonymous_scientific_post_search_uses_anon_read(rate_limited_client):
    """POST /scientific/.../search is a read in spirit — anon_read bucket."""
    for _ in range(settings.rate_limit_anon_read_per_minute):
        r = rate_limited_client.post(_SCIENTIFIC_POST_SEARCH_PATH, json={})
        assert r.status_code != 429

    blocked = rate_limited_client.post(_SCIENTIFIC_POST_SEARCH_PATH, json={})
    assert blocked.status_code == 429
    assert blocked.json()["bucket"] == "anon_read"


# ---------------------------------------------------------------------------
# Bucket: auth_read
# ---------------------------------------------------------------------------


def test_authenticated_scientific_get_uses_auth_read(rate_limited_client):
    """X-API-Key present + scientific GET → auth_read."""
    headers = {"X-API-Key": "bogus"}
    # Drain the anonymous bucket first so we can prove the auth header
    # shifts us into a different bucket with a separate budget.
    for _ in range(settings.rate_limit_anon_read_per_minute):
        rate_limited_client.get(_SCIENTIFIC_GET_PATH)
    blocked_anon = rate_limited_client.get(_SCIENTIFIC_GET_PATH)
    assert blocked_anon.status_code == 429

    for _ in range(settings.rate_limit_auth_read_per_minute):
        r = rate_limited_client.get(_SCIENTIFIC_GET_PATH, headers=headers)
        assert r.status_code != 429
    over = rate_limited_client.get(_SCIENTIFIC_GET_PATH, headers=headers)
    assert over.status_code == 429
    assert over.json()["bucket"] == "auth_read"


def test_authenticated_scientific_post_search_uses_auth_read(rate_limited_client):
    """Authenticated POST search lands in auth_read, not auth_write."""
    headers = {"X-API-Key": "bogus"}
    for _ in range(settings.rate_limit_auth_read_per_minute):
        r = rate_limited_client.post(
            _SCIENTIFIC_POST_SEARCH_PATH, json={}, headers=headers
        )
        assert r.status_code != 429
    over = rate_limited_client.post(
        _SCIENTIFIC_POST_SEARCH_PATH, json={}, headers=headers
    )
    assert over.status_code == 429
    assert over.json()["bucket"] == "auth_read"


def test_authenticated_non_public_non_mutating_route_uses_auth_read(
    rate_limited_client,
):
    """Locks the policy choice: authenticated GET on a non-public-read
    path falls back to ``auth_read``, not a hypothetical ``auth_other``
    and not the stricter ``auth_write``.
    """
    headers = {"X-API-Key": "bogus"}
    # /api/v1/admin/users is an authenticated admin GET — exactly the
    # kind of route the fallback decision was made for. It will return
    # 401 for the bogus key, but the limiter classification fires
    # first and surfaces in 429 once the budget is exhausted.
    path = "/api/v1/admin/users"
    for _ in range(settings.rate_limit_auth_read_per_minute):
        r = rate_limited_client.get(path, headers=headers)
        assert r.status_code != 429
    blocked = rate_limited_client.get(path, headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["bucket"] == "auth_read"


# ---------------------------------------------------------------------------
# Bucket: auth_write
# ---------------------------------------------------------------------------


def test_authenticated_mutating_route_uses_auth_write(rate_limited_client):
    """Authenticated POST upload → auth_write (the tightest auth bucket)."""
    headers = {"X-API-Key": "bogus"}
    for _ in range(settings.rate_limit_auth_write_per_minute):
        r = rate_limited_client.post(_AUTH_WRITE_PATH, json={}, headers=headers)
        assert r.status_code != 429
    blocked = rate_limited_client.post(_AUTH_WRITE_PATH, json={}, headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["bucket"] == "auth_write"


# ---------------------------------------------------------------------------
# Bucket: anon_other
# ---------------------------------------------------------------------------


def test_anonymous_mutating_route_uses_anon_other_not_anon_read(
    rate_limited_client,
):
    """An anonymous POST upload must not inherit the read budget.

    This is the load-bearing test for the route-class split: with the
    old generic ``anon`` bucket, an anonymous scraper could write at
    the read-budget rate. Now anonymous mutations live in the much
    tighter ``anon_other`` bucket.
    """
    for _ in range(settings.rate_limit_anon_other_per_minute):
        r = rate_limited_client.post(_ANON_MUTATING_PATH, json={})
        assert r.status_code != 429
    blocked = rate_limited_client.post(_ANON_MUTATING_PATH, json={})
    assert blocked.status_code == 429
    assert blocked.json()["bucket"] == "anon_other"


# ---------------------------------------------------------------------------
# Identity behavior
# ---------------------------------------------------------------------------


def test_authenticated_identity_uses_credential_fingerprint(rate_limited_client):
    """Two distinct API keys → two distinct auth_read buckets."""
    # Drain key A.
    headers_a = {"X-API-Key": "alpha"}
    for _ in range(settings.rate_limit_auth_read_per_minute):
        r = rate_limited_client.get(_SCIENTIFIC_GET_PATH, headers=headers_a)
        assert r.status_code != 429
    blocked = rate_limited_client.get(_SCIENTIFIC_GET_PATH, headers=headers_a)
    assert blocked.status_code == 429

    # Key B starts fresh — proves identity is the credential, not the IP.
    headers_b = {"X-API-Key": "beta"}
    r = rate_limited_client.get(_SCIENTIFIC_GET_PATH, headers=headers_b)
    assert r.status_code != 429


def test_anonymous_identity_uses_client_ip(rate_limited_client):
    """All anonymous requests from the same transport peer share a bucket.

    The integration-level proof of IP keying lives in
    ``test_api_rate_limit_proxy_headers.py``; here we just verify that
    repeated anonymous hits accumulate against a single counter
    instead of one-counter-per-request.
    """
    for _ in range(settings.rate_limit_anon_read_per_minute):
        rate_limited_client.get(_SCIENTIFIC_GET_PATH)
    blocked = rate_limited_client.get(_SCIENTIFIC_GET_PATH)
    assert blocked.status_code == 429


# ---------------------------------------------------------------------------
# Bypass behavior
# ---------------------------------------------------------------------------


def test_health_endpoint_is_not_rate_limited(rate_limited_client):
    """Operators ping /health on a loop; the limiter must let those pass."""
    for _ in range(settings.rate_limit_anon_read_per_minute + 5):
        r = rate_limited_client.get("/api/v1/health")
        assert r.status_code == 200


def test_rate_limit_can_be_disabled(rate_limited_client, monkeypatch):
    """Flipping the master switch off short-circuits the middleware."""
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    reset_rate_limit_store()
    for _ in range(10):  # well over every bucket budget
        r = rate_limited_client.get(_SCIENTIFIC_GET_PATH)
        assert r.status_code != 429


# ---------------------------------------------------------------------------
# 429 envelope
# ---------------------------------------------------------------------------


def test_429_envelope_shape(rate_limited_client):
    """The 429 body keeps its documented shape across the bucket split."""
    for _ in range(settings.rate_limit_anon_read_per_minute):
        rate_limited_client.get(_SCIENTIFIC_GET_PATH)
    blocked = rate_limited_client.get(_SCIENTIFIC_GET_PATH)
    assert blocked.status_code == 429
    body = blocked.json()
    assert set(body.keys()) >= {
        "detail",
        "code",
        "bucket",
        "retry_after_seconds",
    }
    assert body["code"] == "rate_limit_exceeded"
    assert isinstance(body["retry_after_seconds"], int)
    assert blocked.headers.get("retry-after") == str(body["retry_after_seconds"])


# ---------------------------------------------------------------------------
# Default budgets (no monkeypatch)
# ---------------------------------------------------------------------------


def test_default_budgets_match_spec():
    """Lock the documented defaults so a config drift fails loudly."""
    fresh = settings.__class__()  # bypass any test monkeypatches
    assert fresh.rate_limit_anon_read_per_minute == 60
    assert fresh.rate_limit_auth_read_per_minute == 300
    assert fresh.rate_limit_auth_write_per_minute == 30
    assert fresh.rate_limit_anon_other_per_minute == 20
    assert fresh.rate_limit_auth_login_per_minute == 10
    assert fresh.rate_limit_register_per_hour == 10
