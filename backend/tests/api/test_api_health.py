"""Tests for the liveness (/health) and readiness (/readyz) endpoints."""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

from app.api.routes import health as health_route


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_ready(client):
    """Readiness returns the installed Alembic revision when DB is up."""
    response = client.get("/api/v1/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    assert isinstance(body["alembic_revision"], str)
    assert body["alembic_revision"]  # non-empty


def test_readyz_does_not_leak_database_url(client):
    """The readiness body must not contain the DB URL, password, or host."""
    response = client.get("/api/v1/readyz")
    body_text = response.text
    forbidden = (
        "postgresql",
        "psycopg",
        "tckdb_test",
        "127.0.0.1",
        "password",
    )
    for token in forbidden:
        assert token not in body_text.lower(), (
            f"readyz body leaked {token!r}: {body_text!r}"
        )


def test_readyz_returns_503_when_db_unreachable(client, monkeypatch):
    """If the DB connection fails, readyz returns a stable 503 envelope."""

    class _BoomSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("boom"))

        def close(self) -> None:
            return None

    monkeypatch.setattr(health_route, "SessionLocal", lambda: _BoomSession())

    response = client.get("/api/v1/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body == {
        "status": "not_ready",
        "database": "error",
        "code": "database_unavailable",
    }


def test_readyz_returns_503_when_schema_uninitialized(client, monkeypatch):
    """If alembic_version is missing, readyz reports schema_not_initialized."""

    class _NoSchemaSession:
        def __init__(self) -> None:
            self._calls = 0

        def execute(self, *_args, **_kwargs):
            self._calls += 1
            if self._calls == 1:
                # SELECT 1 succeeds.
                class _Result:
                    def scalar_one_or_none(self):
                        return 1

                return _Result()
            # alembic_version lookup blows up as if the table is missing.
            raise OperationalError(
                "SELECT version_num FROM alembic_version", {}, Exception("no table")
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(health_route, "SessionLocal", lambda: _NoSchemaSession())

    response = client.get("/api/v1/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body == {
        "status": "not_ready",
        "database": "ok",
        "code": "schema_not_initialized",
    }
