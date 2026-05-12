"""Tests for F13 — PostgreSQL ``statement_timeout`` listener.

Two thin checks:

- A freshly-bound SQLAlchemy engine with the listener installed
  reports the configured ``statement_timeout`` on each session.
- The :class:`OperationalError` handler maps SQLSTATE 57014
  (``query_canceled``) to a sanitized 503 ``query_timeout`` response
  without leaking the offending SQL.

We don't try to provoke a real query-cancel in the test suite — that
needs an artificially expensive query and is brittle. The handler
is exercised by simulating the wrapped driver exception directly.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.deps import _install_statement_timeout_listener
from app.api.errors import register_exception_handlers


# ---------------------------------------------------------------------------
# Listener applies SET statement_timeout
# ---------------------------------------------------------------------------


def _build_engine_with_listener(timeout_ms: int | None):
    """Create a one-shot engine and install the timeout listener.

    Uses the test DB connection so the listener actually runs
    against PostgreSQL.
    """
    eng = create_engine(settings.database_url, pool_pre_ping=True)
    # Patch the module-level setting for the duration of the listener
    # registration; SQLAlchemy reads the closure value when ``connect``
    # fires.
    previous = settings.db_statement_timeout_ms
    settings.db_statement_timeout_ms = timeout_ms
    try:
        _install_statement_timeout_listener(eng)
        return eng
    finally:
        settings.db_statement_timeout_ms = previous


def test_listener_applies_configured_timeout():
    eng = _build_engine_with_listener(15_000)
    try:
        with Session(eng) as s:
            value = s.execute(text("SHOW statement_timeout")).scalar()
        # PostgreSQL normalizes 15000 → "15s".
        assert value in {"15s", "15000ms"}
    finally:
        eng.dispose()


def test_listener_zero_disables_app_level_setting():
    """``db_statement_timeout_ms = 0`` skips the SET; role default wins."""
    eng = _build_engine_with_listener(0)
    try:
        with Session(eng) as s:
            value = s.execute(text("SHOW statement_timeout")).scalar()
        # We don't assert a specific value — only that the listener
        # didn't crash and the session is usable. The role-level value
        # is whatever the operator set.
        assert isinstance(value, str)
    finally:
        eng.dispose()


# ---------------------------------------------------------------------------
# OperationalError handler — sanitized response on query timeout
# ---------------------------------------------------------------------------


class _FakeQueryCanceled(Exception):
    """Stand-in for psycopg's ``errors.QueryCanceled`` (SQLSTATE 57014)."""

    sqlstate = "57014"


@pytest.fixture
def operational_error_client() -> TestClient:
    """Tiny app that lets us trigger the handler with a controlled SQLSTATE."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-timeout")
    def _raise_timeout():
        raise OperationalError(
            statement="SELECT very_expensive(*) FROM secret_table",
            params=None,
            orig=_FakeQueryCanceled(),
        )

    @app.get("/raise-other-op")
    def _raise_other():
        # No sqlstate → falls back to ``database_unavailable``.
        class _BareOp(Exception):
            pass
        raise OperationalError(
            statement="SELECT bare_op()", params=None, orig=_BareOp()
        )

    return TestClient(app, raise_server_exceptions=False)


def test_query_timeout_returns_sanitized_503(operational_error_client):
    r = operational_error_client.get("/raise-timeout")
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "query_timeout"
    # SQL must not leak.
    assert "SELECT" not in body["detail"]
    assert "secret_table" not in repr(body)
    assert "very_expensive" not in repr(body)


def test_generic_operational_error_returns_database_unavailable(
    operational_error_client,
):
    r = operational_error_client.get("/raise-other-op")
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "database_unavailable"
    assert "bare_op" not in repr(body)
    assert "SELECT" not in body["detail"]
