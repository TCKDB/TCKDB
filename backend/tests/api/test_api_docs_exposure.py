"""Tests for the OpenAPI/Swagger/ReDoc exposure gate (F8).

``settings.expose_api_docs`` defaults to True (local/dev). Hosted
production sets it to False. When False, FastAPI must not register
``/docs``, ``/redoc``, or ``/openapi.json``.
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
    def _build() -> TestClient:
        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_write_db] = lambda: db_session
        return TestClient(app)

    return _build


def test_docs_enabled_exposes_routes(_client_factory, monkeypatch):
    monkeypatch.setattr(settings, "expose_api_docs", True)
    with _client_factory() as c:
        assert c.get("/openapi.json").status_code == 200
        assert c.get("/docs").status_code == 200
        assert c.get("/redoc").status_code == 200


def test_docs_disabled_hides_routes(_client_factory, monkeypatch):
    monkeypatch.setattr(settings, "expose_api_docs", False)
    with _client_factory() as c:
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/docs").status_code == 404
        assert c.get("/redoc").status_code == 404


def test_scientific_routes_work_when_docs_disabled(_client_factory, monkeypatch):
    """Disabling docs must not affect the scientific read API."""
    monkeypatch.setattr(settings, "expose_api_docs", False)
    with _client_factory() as c:
        r = c.get("/api/v1/health")
        assert r.status_code == 200
