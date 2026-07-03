"""Tests for the legacy entity-read auth gate (F14).

When ``LEGACY_READS_REQUIRE_AUTH=true`` the legacy
``/api/v1/{thermo,kinetics,...}`` routes return 401 to anonymous
callers; ``/api/v1/scientific/*`` is unaffected.

The autouse fixture in ``conftest.py`` sets the flag to ``false`` for
every test by default; these tests flip it back on via monkeypatch
and build a fresh app (the flag is read at request time through
``settings``, so no full restart is needed).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_db, get_write_db


@pytest.fixture
def gated_client(db_session: Session, monkeypatch):
    monkeypatch.setattr(settings, "legacy_reads_require_auth", True)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def test_anonymous_legacy_thermo_returns_401(gated_client):
    """Anonymous GET on the legacy thermo list route is rejected."""
    r = gated_client.get("/api/v1/thermo")
    assert r.status_code == 401


def test_anonymous_legacy_kinetics_returns_401(gated_client):
    r = gated_client.get("/api/v1/kinetics")
    assert r.status_code == 401


def test_anonymous_legacy_geometries_returns_401(gated_client):
    r = gated_client.get("/api/v1/geometries")
    assert r.status_code == 401


def test_anonymous_legacy_literature_returns_401(gated_client):
    r = gated_client.get("/api/v1/literature")
    assert r.status_code == 401


def test_scientific_routes_remain_public_when_legacy_gated(gated_client):
    """The public scientific surface must not be affected by the gate."""
    r = gated_client.get(
        "/api/v1/scientific/reactions/search?reactants=A&products=B"
    )
    # Either a 200 (no matches) or a 422 (chemistry didn't resolve).
    # The point is: not 401.
    assert r.status_code != 401


def test_authenticated_legacy_route_succeeds(
    db_session, monkeypatch, _api_test_user
):
    """A request bearing a valid API-key should pass the gate."""
    from app.db.models.api_key import ApiKey

    monkeypatch.setattr(settings, "legacy_reads_require_auth", True)
    # Mint a fresh raw key + matching hash so the gate's
    # ``authenticate_api_key`` call resolves it.
    import hashlib
    raw_key = "phase-2-legacy-auth-test-key"
    db_session.add(
        ApiKey(
            user_id=_api_test_user,
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            label="phase-2-test",
        )
    )
    db_session.flush()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        r = c.get("/api/v1/thermo", headers={"X-API-Key": raw_key})
        assert r.status_code == 200


def test_setting_off_keeps_legacy_routes_anonymous(db_session, monkeypatch):
    """With the setting off (local/dev), no auth is required."""
    monkeypatch.setattr(settings, "legacy_reads_require_auth", False)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        r = c.get("/api/v1/thermo")
        assert r.status_code == 200
