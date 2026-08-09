from __future__ import annotations

import hashlib
import os
import re
import socket
import subprocess
import warnings
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


_TEST_DATABASE_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")


def _validate_test_db_name(db_name: str) -> str:
    """Permit destructive fixture setup only for isolated test databases."""
    if not _TEST_DATABASE_NAME.fullmatch(db_name):
        raise ValueError(
            "DB_TEST_NAME must match isolated test-database pattern "
            "'tckdb_test' or 'tckdb_test_<alnum_or_underscore>'."
        )
    return db_name


# ---------------------------------------------------------------------------
# Ownership marker
#
# Every database this fixture creates is stamped with a comment recording the
# host and pid of the pytest process that created it.  The marker is what makes
# the startup sweep safe: it lets the sweep reclaim *only* databases this
# harness is responsible for, and lets it tell a genuinely abandoned database
# apart from one a concurrently-starting pytest run has just created but not
# yet connected to.  Databases without a marker are never swept.
# ---------------------------------------------------------------------------

_MARKER_PREFIX = "tckdb-test-harness"
_MARKER_PATTERN = re.compile(rf"^{re.escape(_MARKER_PREFIX)} host=(\S+) pid=(\d+)$")


def _safe_host() -> str:
    """Hostname reduced to characters safe inside a SQL string literal."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", socket.gethostname()) or "unknown"


def _ownership_marker() -> str:
    return f"{_MARKER_PREFIX} host={_safe_host()} pid={os.getpid()}"


def _pid_is_running(pid: int) -> bool:
    """True if a process with this pid currently exists on this host."""
    if pid <= 0:
        return True  # Unparseable/implausible — treat as live and skip.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Exists, owned by another user.
    except OSError:
        return True
    return True


def _marker_is_reclaimable(marker: str | None) -> bool:
    """Only reclaim databases stamped by *this host* whose creator pid is gone.

    A missing or foreign marker means the database was not created by this
    harness (or was created on a different host, where we cannot reason about
    pids at all) — in both cases the conservative answer is "leave it alone".
    """
    if not marker:
        return False
    match = _MARKER_PATTERN.match(marker.strip())
    if match is None:
        return False
    host, pid = match.group(1), match.group(2)
    if host != _safe_host():
        return False
    return not _pid_is_running(int(pid))


def _sweep_stale_test_databases(current_db_name: str) -> None:
    """Drop databases this harness created but never got to drop.

    The fixture's ``finally`` handles every in-process failure path; this
    sweep exists only for the paths no Python code can cover — ``SIGKILL``,
    an OOM kill, a power loss.  It is deliberately paranoid:

    * only databases carrying this harness's ownership marker are eligible;
    * only markers written by *this host* with a creator pid that is no
      longer running;
    * only names matching the isolated-test-database pattern;
    * never the database this session is about to use;
    * the ``DROP`` is issued *without* terminating backends, so if a
      connection appears in the race window Postgres refuses and we skip —
      the server, not this code, is the final arbiter of "in use".

    Best-effort throughout: a sweep failure must never fail a test session.
    Set ``TCKDB_TEST_DB_SWEEP=0`` to disable.
    """
    if os.environ.get("TCKDB_TEST_DB_SWEEP", "1") == "0":
        return

    engine = create_engine(_database_url("postgres"), future=True, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            candidates = connection.execute(
                text(r"""
                    SELECT d.datname,
                           shobj_description(d.oid, 'pg_database') AS marker
                    FROM pg_database d
                    WHERE d.datname LIKE 'tckdb\_test%'
                      AND NOT d.datistemplate
                      AND NOT EXISTS (
                          SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname
                      )
                """)
            ).all()

            for datname, marker in candidates:
                if datname == current_db_name:
                    continue
                if not _TEST_DATABASE_NAME.fullmatch(datname):
                    continue
                if not _marker_is_reclaimable(marker):
                    continue
                try:
                    # No pg_terminate_backend here: a database that acquired a
                    # connection since the query above must survive.
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{datname}"'))
                except Exception:
                    continue
    except Exception:
        return
    finally:
        engine.dispose()


def _recreate_test_database(db_name: str) -> None:
    db_name = _validate_test_db_name(db_name)
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
            # Stamp ownership immediately so an abandoned database is always
            # identifiable, even if the process dies on the very next line.
            connection.execute(
                text(f"COMMENT ON DATABASE \"{db_name}\" IS '{_ownership_marker()}'")
            )
    finally:
        engine.dispose()


def _drop_test_database(db_name: str) -> None:
    """Remove the per-run database after pytest releases pooled connections."""
    db_name = _validate_test_db_name(db_name)
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
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def db_engine():
    db_name = _resolve_test_db_name()
    # Export the resolved name so subprocess-based tests (e.g. the bundle
    # export CLI smoke test) inherit the same database without needing
    # their own resolution logic.
    os.environ["DB_TEST_NAME"] = db_name
    _sweep_stale_test_databases(db_name)

    engine = None
    # Creation and migration live *inside* the ``try`` so ``finally`` covers
    # every in-process failure path, not just a clean session exit: a failed
    # ``alembic upgrade``, a failed ``create_engine``, and any exception
    # (collection error, KeyboardInterrupt) thrown into this generator.
    # Previously these ran before the ``try`` and each one leaked a fully
    # migrated database under a name that is never reused.
    body_failed = False
    try:
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
        yield engine
    except BaseException:
        body_failed = True
        raise
    finally:
        # Cleanup must never mask why the body failed. Python replaces a
        # propagating exception with anything raised from ``finally``, so a
        # secondary "could not drop" error would bury the failed
        # ``alembic upgrade`` that is the thing worth reading. Now that
        # migrations run inside the ``try``, that path is reachable.
        #
        # A cleanup failure is still a leak, so it is never swallowed
        # silently: when the body succeeded it propagates as before, and when
        # the body failed it is downgraded to a warning so both survive.
        for cleanup in (
            lambda: engine.dispose() if engine is not None else None,
            lambda: _drop_test_database(db_name),
        ):
            try:
                cleanup()
            except Exception as cleanup_error:
                if not body_failed:
                    raise
                warnings.warn(
                    f"test-database cleanup failed for {db_name!r} while another error was "
                    f"propagating; the database may have leaked: {cleanup_error!r}",
                    RuntimeWarning,
                    stacklevel=2,
                )


@pytest.fixture
def db_conn(db_engine) -> Iterator[Connection]:
    """A connection inside a transaction that is always rolled back.

    Two nested scopes are opened deliberately:

    ``begin()``
        The per-test transaction. Rolling it back at teardown is what makes
        the shared, session-scoped database safe to reuse — nothing a test
        writes survives it, whether the test committed or not.

    ``begin_nested()``
        A SAVEPOINT wrapped around the whole test. This is not about undoing
        anything; it changes how ``Session(db_conn)`` behaves. SQLAlchemy's
        default ``join_transaction_mode="conditional_savepoint"`` picks
        ``"rollback_only"`` for a plain in-transaction Connection, which means
        one ``session.rollback()`` — including the implicit one when a test
        asserts that an upload raises — tears down the *outer* transaction and
        leaves the connection unusable for the rest of the test. With a
        SAVEPOINT already in progress the same default resolves to
        ``"create_savepoint"``, so each ``Session`` gets its own nested scope:
        its commits stay inside the per-test transaction and its rollbacks
        undo only its own work.

    That second property is what lets test bodies keep writing
    ``with Session(db_conn) as session, session.begin(): ...`` unchanged while
    no longer committing anything to the shared database.
    """
    with db_engine.connect() as connection:
        transaction = connection.begin()
        connection.begin_nested()
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
