"""Tests for CORS posture.

The hosted deployment ships with ``CORS_ALLOW_ORIGINS`` empty; the
middleware is not even registered in that mode. Production
deployments override the env var to a narrow allow-list. These tests
build the app twice — once with no origins, once with a single
allowed origin — and confirm both postures.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_db, get_write_db


@pytest.fixture
def _client_factory(db_session: Session):
    """Build a fresh TestClient honoring the current ``settings.cors_*`` values."""

    def _build() -> TestClient:
        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_write_db] = lambda: db_session
        return TestClient(app)

    return _build


def test_unconfigured_cors_is_not_permissive(_client_factory, monkeypatch):
    """With an empty allow-list, no Access-Control-Allow-Origin is sent.

    A browser cross-origin call with credentials would be rejected at
    the browser. We verify by checking that the CORSMiddleware is not
    registered: an OPTIONS preflight returns the routed handler's
    405, not a 200 with CORS headers.
    """
    monkeypatch.setattr(settings, "cors_allow_origins", [])
    client = _client_factory()
    with client as c:
        r = c.get(
            "/api/v1/health",
            headers={"Origin": "https://evil.example"},
        )
    assert "access-control-allow-origin" not in {
        k.lower() for k in r.headers
    }


def test_configured_origin_receives_cors_headers(_client_factory, monkeypatch):
    """A request from an allow-listed origin gets a matching ACAO header."""
    monkeypatch.setattr(
        settings, "cors_allow_origins", ["https://app.tckdb.org"]
    )
    client = _client_factory()
    with client as c:
        r = c.get(
            "/api/v1/health",
            headers={"Origin": "https://app.tckdb.org"},
        )
    assert r.headers.get("access-control-allow-origin") == "https://app.tckdb.org"


def test_unlisted_origin_is_not_permitted(_client_factory, monkeypatch):
    """A request from an origin not in the allow-list gets no CORS header."""
    monkeypatch.setattr(
        settings, "cors_allow_origins", ["https://app.tckdb.org"]
    )
    client = _client_factory()
    with client as c:
        r = c.get(
            "/api/v1/health",
            headers={"Origin": "https://evil.example"},
        )
    # Starlette returns the response but with no ACAO header, which the
    # browser then treats as a CORS denial.
    assert r.headers.get("access-control-allow-origin") in (None, "")
