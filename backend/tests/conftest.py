from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_current_user, get_db, get_write_db
from app.api.rate_limit import reset_rate_limit_store
from app.db.models.api_key import ApiKey
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _disable_rate_limit_by_default():
    """Disable the public rate limiter for every test by default.

    The middleware uses an in-process store keyed by client IP — under
    the TestClient every request comes from the same loopback host, so
    a 60/min anonymous budget would otherwise reject test #61.  Tests
    that exercise the limiter explicitly opt back in (see
    ``backend/tests/api/test_api_rate_limiting.py``).
    """
    previous = settings.rate_limit_enabled
    settings.rate_limit_enabled = False
    reset_rate_limit_store()
    try:
        yield
    finally:
        settings.rate_limit_enabled = previous
        reset_rate_limit_store()


@pytest.fixture(autouse=True)
def _security_phase2_test_defaults():
    """Relax Phase 2 production-only defaults for the test suite.

    The hosted production posture requires a credential for the
    legacy ``/api/v1/{thermo,kinetics,...}`` routes and emits secure
    cookies. Both break the test fixtures (anonymous TestClient over
    HTTP). Tests opt back into the production posture by flipping
    these flags via monkeypatch in their own scope.
    """
    previous_legacy = settings.legacy_reads_require_auth
    previous_secure = settings.session_cookie_secure
    settings.legacy_reads_require_auth = False
    settings.session_cookie_secure = False
    try:
        yield
    finally:
        settings.legacy_reads_require_auth = previous_legacy
        settings.session_cookie_secure = previous_secure


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("DB_USER", "tckdb")
    env.setdefault("DB_PASSWORD", "tckdb")
    env.setdefault("DB_HOST", "127.0.0.1")
    env.setdefault("DB_PORT", "5432")
    return env


def _db_env(db_name: str) -> dict[str, str]:
    env = _base_env()
    env["DB_NAME"] = db_name
    return env


def _database_url(db_name: str) -> str:
    env = _db_env(db_name)
    return (
        f"postgresql+psycopg://{env['DB_USER']}:{env['DB_PASSWORD']}"
        f"@{env['DB_HOST']}:{env['DB_PORT']}/{env['DB_NAME']}"
        "?client_encoding=utf8"
    )


def _resolve_test_db_name() -> str:
    """Derive a test-DB name that won't collide across concurrent runners.

    Precedence:

    1. Explicit ``DB_TEST_NAME`` — used verbatim for backward compatibility.
       Explicit names are single-tenant; do not point two concurrent pytest
       runs at the same value on one Postgres host (see ``docs/testing.md``).
    2. ``PYTEST_XDIST_WORKER`` — pytest-xdist worker id (e.g. ``gw0``),
       producing ``tckdb_test_<worker>``. Sanitized so any value Postgres
       would reject becomes safe identifier characters.
    3. Fallback — ``tckdb_test_<pid>`` so two ad-hoc pytest processes on
       one host never share a database, even without xdist.
    """
    explicit = os.environ.get("DB_TEST_NAME")
    if explicit:
        return explicit

    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker:
        safe_worker = re.sub(r"[^A-Za-z0-9_]+", "_", worker)
        return f"tckdb_test_{safe_worker}"

    return f"tckdb_test_{os.getpid()}"


def _recreate_test_database(db_name: str) -> None:
    admin_url = _database_url("postgres")
    engine = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")

    try:
        with engine.connect() as connection:
            connection.execute(
                text("""
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :db_name
                      AND pid <> pg_backend_pid()
                    """),
                {"db_name": db_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            connection.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def db_engine():
    db_name = _resolve_test_db_name()
    # Export the resolved name so subprocess-based tests (e.g. the bundle
    # export CLI smoke test) inherit the same database without needing
    # their own resolution logic.
    os.environ["DB_TEST_NAME"] = db_name
    _recreate_test_database(db_name)

    subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=_db_env(db_name),
        check=True,
        capture_output=True,
        text=True,
    )

    engine = create_engine(_database_url(db_name), future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_conn(db_engine) -> Iterator[Connection]:
    with db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


# ---------------------------------------------------------------------------
# API test fixtures
# ---------------------------------------------------------------------------

_TEST_API_KEY = "test-api-key-for-tckdb"
_TEST_API_KEY_HASH = hashlib.sha256(_TEST_API_KEY.encode()).hexdigest()


@pytest.fixture(scope="session")
def _api_test_user(db_engine) -> int:
    """Create a regular-role test user with an API key once per session.

    Committed so it's visible to all test-scoped sessions.
    """
    with Session(db_engine) as session:
        with session.begin():
            user = AppUser(
                username="testuser",
                role=AppUserRole.user,
            )
            session.add(user)
            session.flush()
            session.add(
                ApiKey(
                    user_id=user.id,
                    key_hash=_TEST_API_KEY_HASH,
                    label="pytest session key",
                )
            )
            session.flush()
            user_id = user.id
    return user_id


def _create_user_in_session(session: Session, *, username: str, role: AppUserRole) -> int:
    """Create an AppUser inside the per-test transaction and return its id.

    Function-scoped so the user is rolled back at end-of-test — avoids
    leaking curator/admin rows into tests like ``bootstrap_admin`` that
    assert on the absence of any admin user.
    """
    user = AppUser(username=username, role=role)
    session.add(user)
    session.flush()
    return user.id


@pytest.fixture
def _api_curator_user(db_session) -> int:
    """Curator-role user, created per-test in the rollback transaction."""
    return _create_user_in_session(
        db_session, username="testcurator", role=AppUserRole.curator
    )


@pytest.fixture
def _api_admin_user(db_session) -> int:
    """Admin-role user, created per-test in the rollback transaction."""
    return _create_user_in_session(
        db_session, username="testadmin", role=AppUserRole.admin
    )


@pytest.fixture
def _api_other_user(db_session) -> int:
    """A second regular-role user, created per-test for cross-user 403 checks."""
    return _create_user_in_session(
        db_session, username="testother", role=AppUserRole.user
    )


@pytest.fixture
def client(db_engine, _api_test_user) -> Iterator[TestClient]:
    """TestClient with per-test transaction rollback.

    The session is bound to a connection with an open transaction that is
    rolled back after the test, so no data persists between tests.
    """
    app = create_app()

    connection = db_engine.connect()
    transaction = connection.begin()
    # Savepoint mode: a flush/commit error inside the session releases its
    # SAVEPOINT instead of rolling back the outer transaction. Without this,
    # an IntegrityError in any test would deassociate the outer transaction
    # and leak the test user (and anything else committed in this run) once
    # the connection was returned to the pool.
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    # Override both DB dependencies to use our transactional session
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_write_db] = lambda: session

    # Override auth to return the pre-seeded test user
    test_user = session.get(AppUser, _api_test_user)
    app.dependency_overrides[get_current_user] = lambda: test_user

    with TestClient(app) as c:
        c._db_session = session  # expose for tests that need raw inserts
        yield c

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def db_session(client) -> Session:
    """The same DB session used by the TestClient, for raw ORM inserts
    that need to be visible to the API endpoints in the same transaction."""
    return client._db_session


@pytest.fixture
def login_as(client, db_session):
    """Helper to swap ``get_current_user`` mid-test on the shared client.

    Returns a callable ``login_as(user_id)`` that re-overrides the auth
    dependency so subsequent requests run as the given user. Useful for
    tests that need to act as multiple roles (e.g. user creates a
    submission, curator approves it) within one transaction.
    """
    def _login_as(user_id: int) -> AppUser:
        user = db_session.get(AppUser, user_id)
        client.app.dependency_overrides[get_current_user] = lambda: user
        return user

    return _login_as
