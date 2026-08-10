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


#: PostgreSQL truncates identifiers at ``NAMEDATALEN - 1`` bytes and does it
#: silently, which would collapse two long per-worker names onto one database.
_MAX_IDENTIFIER_BYTES = 63


def _sanitize_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value)


def _with_worker_suffix(base: str, safe_worker: str) -> str:
    """Append the xdist worker id to ``base``, keeping the result distinct.

    Postgres truncates over-long identifiers rather than rejecting them, so
    naively concatenating could hand ``gw10`` and ``gw11`` the same database.
    The base is trimmed instead — the suffix, which is what makes the name
    unique, is always preserved intact.
    """
    suffix = f"_{safe_worker}"
    budget = _MAX_IDENTIFIER_BYTES - len(suffix)
    if budget < 1:
        # Pathologically long worker id: the suffix alone is the identity.
        return f"tckdb_test{suffix}"[:_MAX_IDENTIFIER_BYTES]
    return f"{base[:budget]}{suffix}"


def _resolve_test_db_name() -> str:
    """Derive a test-DB name that won't collide across concurrent runners.

    Precedence:

    1. ``DB_TEST_NAME`` **plus** ``PYTEST_XDIST_WORKER`` — the explicit name
       with the worker id appended, e.g. ``tckdb_test_api_991_gw3``. This case
       comes first deliberately. Every gate script and CI job sets
       ``DB_TEST_NAME`` (see ``.github/workflows/backend-ci.yml``), and while
       the explicit name won unconditionally, turning on ``-n`` pointed all
       workers at one database: they raced to ``DROP``/``CREATE`` it during
       session setup, and whichever survived was then written concurrently by
       every worker — reintroducing exactly the cross-test visibility the
       per-test rollback exists to prevent. An operator asking for a specific
       database name and asking for N workers is asking for N databases.
    2. Explicit ``DB_TEST_NAME`` alone — used verbatim. Single-tenant: do not
       point two concurrent pytest runs at the same value on one Postgres host
       (see ``docs/testing.md``).
    3. ``PYTEST_XDIST_WORKER`` alone — pytest-xdist worker id (e.g. ``gw0``),
       producing ``tckdb_test_<worker>``. Sanitized so any value Postgres
       would reject becomes safe identifier characters.
    4. Fallback — ``tckdb_test_<pid>`` so two ad-hoc pytest processes on
       one host never share a database, even without xdist.

    The xdist controller process has ``PYTEST_XDIST_WORKER`` unset and never
    creates a database (it collects but does not run tests), so only the
    workers reach cases 1 and 3.
    """
    explicit = os.environ.get("DB_TEST_NAME")
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    safe_worker = _sanitize_identifier(worker) if worker else None

    if explicit and safe_worker:
        return _with_worker_suffix(explicit, safe_worker)
    if explicit:
        return explicit
    if safe_worker:
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
# Committed-row tripwire
#
# One database is shared by the whole pytest process, so anything a test
# *commits* is visible to every test that runs after it.  Hundreds of tests in
# this repo locate "the" row they just wrote with an unqualified query —
# ``session.scalar(select(Calculation).where(Calculation.type == irc))`` — which
# is only correct while the database holds nothing but the current test's own
# writes.  A single committing test therefore turns the suite order-dependent,
# and the failure surfaces in whatever unrelated file ``pytest-randomly``
# happens to schedule next.
#
# This fixture blames the test that commits instead.  It started life in
# ``tests/workflows/conftest.py`` (PR #111) and now covers every tree: a
# committing test is named wherever it lives.
#
# Tests that genuinely need two concurrent transactions (the ``*_isolation``
# family, the upload-worker tests) take ``db_engine`` directly and delete their
# rows in a fixture ``finally`` — they satisfy the tripwire because the counts
# match again by teardown, not because they are exempt.
# ---------------------------------------------------------------------------

#: Tables watched for committed growth.  Deliberately a tripwire rather than an
#: audit: counting all ~110 public tables costs ~90 ms per probe (~12 min over
#: the suite), while this curated union costs ~2 ms.  Any commit big enough to
#: confuse a later test lands in at least one of these.
#:
#: ``app_user``/``api_key`` are intentionally absent: the session-scoped
#: ``_api_test_user`` fixture commits exactly one user + key on first use, and
#: watching those tables would blame whichever test happened to be first.
_WATCHED_TABLES: tuple[str, ...] = (
    # identity
    "species",
    "species_entry",
    "chem_reaction",
    "reaction_entry",
    "transition_state",
    "transition_state_entry",
    # calculations and artifacts
    "calculation",
    "calculation_artifact",
    "calculation_parameter",
    "geometry",
    "calc_sp_result",
    "calc_opt_result",
    "calc_freq_result",
    # conformers
    "conformer_group",
    "conformer_observation",
    "conformer_selection",
    # scientific products
    "statmech",
    "thermo",
    "kinetics",
    "transport",
    "network",
    "network_solve",
    # provenance
    "software",
    "software_release",
    "workflow_tool",
    "workflow_tool_release",
    "level_of_theory",
    "literature",
    "author",
    "execution_environment_manifest",
    # submission / review / curation
    "submission",
    "submission_record_link",
    "upload_job",
    "idempotency_record",
    "record_review",
    "record_machine_review",
    "species_entry_review",
    "dataset_release",
    "release_selection",
)

_COUNT_SQL = text(
    " UNION ALL ".join(
        f"SELECT '{table}' AS t, count(*) AS n FROM public.{table}"
        for table in _WATCHED_TABLES
    )
)

#: Fixtures whose presence means the test can reach the shared database.
#: ``request.fixturenames`` is the resolved closure, so a test that only takes
#: ``client`` still lists ``db_engine``.  Pure unit tests list none of them and
#: must not be made to create a database.
_DB_FIXTURE_NAMES = frozenset({"db_engine", "db_conn", "db_session", "client"})


@pytest.fixture(scope="session")
def _committed_row_probe(db_engine) -> Iterator[Connection]:
    """A connection that always reads the latest *committed* state.

    ``AUTOCOMMIT`` matters: a pooled connection holding an open transaction
    would keep returning its first snapshot and the tripwire would never fire.
    """
    connection = db_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        # Fail loudly at session start if the watched list has drifted from the
        # schema, rather than silently watching nothing for the whole run.
        connection.execute(_COUNT_SQL)
        yield connection
    finally:
        connection.close()


def _committed_counts(connection) -> dict[str, int]:
    return dict(connection.execute(_COUNT_SQL).all())


@pytest.fixture(autouse=True)
def _refuse_committed_rows(request) -> Iterator[None]:
    """Fail the test that commits, not the test that trips over the residue.

    Autouse, so it is set up before the test's own fixtures and therefore torn
    down after them — the second count is read once ``db_conn``/``client`` has
    rolled back, so only a genuine commit shows up.

    The baseline is taken per test rather than once per session on purpose. A
    session-wide baseline would be perturbed by any *other* test committing in
    between, and the next test would be blamed for a leak it did not cause. A
    tripwire that reports order-dependent false positives would be a strange
    thing to install here.

    Set ``TCKDB_TEST_COMMIT_TRIPWIRE=0`` to disable — for bisecting an
    unrelated failure, never as a way to land a committing test.
    """
    if os.environ.get("TCKDB_TEST_COMMIT_TRIPWIRE", "1") == "0":
        yield
        return
    if _DB_FIXTURE_NAMES.isdisjoint(request.fixturenames):
        yield
        return

    # Resolved here rather than as a parameter so that a test which never
    # touches the database never pays for a connection.
    probe = request.getfixturevalue("_committed_row_probe")
    before = _committed_counts(probe)

    yield

    after = _committed_counts(probe)
    if after == before:
        return

    grew = {
        table: (before[table], after[table])
        for table in after
        if after[table] != before[table]
    }
    raise AssertionError(
        f"{request.node.nodeid} committed rows into the shared test database: "
        + ", ".join(f"{t} {was}->{now}" for t, (was, now) in sorted(grew.items()))
        + ". Tests must persist through the `db_conn`/`db_session` fixtures, "
        "whose transaction is rolled back at teardown — committed rows are "
        "visible to every later test in the process and are what makes this "
        "suite order-dependent. If a test genuinely needs two concurrent "
        "transactions, take `db_engine` and delete its rows in a fixture "
        "`finally`."
    )


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


@pytest.fixture(autouse=True, scope="session")
def _disable_startup_storage_probe():
    """Keep the boot-time object-store probe out of the suite.

    ``create_app()`` runs once per ``client`` fixture, i.e. hundreds of
    times, and the probe is a real network round trip with a 4-second
    ceiling. On a machine with no MinIO that is hours; with MinIO it is
    hundreds of pointless ``head_bucket`` calls. The probe's own
    behaviour — that it runs by default, logs loudly, and never fails
    startup — is covered directly in
    ``tests/api/test_startup_storage_probe.py``.
    """
    os.environ["TCKDB_STARTUP_STORAGE_PROBE"] = "false"
    yield
    os.environ.pop("TCKDB_STARTUP_STORAGE_PROBE", None)


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
