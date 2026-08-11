from __future__ import annotations

import hashlib
import os
import re
import secrets
import socket
import subprocess
import warnings
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api import deps as api_deps
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


#: Environment variable carrying this pytest *run*'s identity.
#:
#: Set once, at conftest import, by whichever process gets there first.  With
#: ``-n``, that is the xdist controller: it imports the rootdir conftest before
#: execnet spawns the workers, and the workers inherit ``os.environ``, so all
#: eight databases of one run share one token.  If a future pytest or xdist
#: stops importing this file in the controller, each worker mints its own token
#: instead — which is still correct, because the token only ever has to differ
#: *between runs*; sharing it within a run buys diagnosis ("these eight
#: databases are one run"), not safety.
_RUN_TOKEN_ENV = "TCKDB_TEST_RUN_TOKEN"


def _mint_run_token() -> str:
    """A short token unique to this pytest run on this host.

    Not a pid: two runs a week apart can share a pid, and — more to the
    point — the pid of the *controller* is not available to a worker that
    mints its own.  Eight random hex characters is 2**32 values; combined
    with the refusal check in :func:`_recreate_test_database`, a collision
    is reported rather than acted on.
    """
    return secrets.token_hex(4)


RUN_TOKEN = os.environ.setdefault(_RUN_TOKEN_ENV, _mint_run_token())


def _with_run_suffix(base: str, suffix: str) -> str:
    """Append ``suffix`` to ``base``, keeping the result distinct.

    Postgres truncates over-long identifiers rather than rejecting them, so
    naively concatenating could hand ``gw10`` and ``gw11`` the same database.
    The base is trimmed instead — the suffix, which is what makes the name
    unique, is always preserved intact.
    """
    budget = _MAX_IDENTIFIER_BYTES - len(suffix)
    if budget < 1:
        # Pathologically long suffix: the suffix alone is the identity.
        return f"tckdb_test{suffix}"[:_MAX_IDENTIFIER_BYTES]
    return f"{base[:budget]}{suffix}"


def _resolve_test_db_name() -> str:
    """Derive a test-DB name unique to this *run*, this worker and this host.

    The name is ``<base>_<run token>[_<worker>]``:

    * **base** is ``DB_TEST_NAME`` when set, otherwise ``tckdb_test``.  An
      explicit ``DB_TEST_NAME`` is a *label*, not the final name — see below.
    * **run token** is :data:`RUN_TOKEN`, minted once per pytest run.
    * **worker** is the sanitized ``PYTEST_XDIST_WORKER`` id when running
      under ``-n``.  Every gate script and CI job sets ``DB_TEST_NAME``, and
      while the explicit name won unconditionally, turning on ``-n`` pointed
      all workers at one database: they raced to ``DROP``/``CREATE`` it during
      session setup, and whichever survived was then written concurrently by
      every worker — reintroducing exactly the cross-test visibility the
      per-test rollback exists to prevent.  Asking for a specific database
      name and for N workers is asking for N databases.

    The run token is why ``DB_TEST_NAME`` can no longer be the whole name.
    Before it, names were unique *within* a run and identical *across* runs:
    two pytest processes on one host — two agents in two worktrees, a
    developer alongside a self-hosted runner — shared eight databases and
    dropped each other's schemas mid-run.  On 2026-08-10 that produced 2305
    errors on a tree that passed alone, and none of them said "another run is
    using your database"; they said ``terminating connection due to
    administrator command``.  Making the *default* unique would not have
    helped, because the collision shape that actually happens is two runs
    copy-pasting the same ``DB_TEST_NAME=...`` out of the same document.

    Over-long names have the base trimmed, never the suffix: Postgres
    truncates identifiers silently, and ``…_gw10``/``…_gw11`` must not
    collapse onto one name.

    The resolved name is exported back to ``DB_TEST_NAME`` by the
    ``db_engine`` fixture, so subprocess-based tests still inherit the exact
    database this session created.

    The xdist controller has ``PYTEST_XDIST_WORKER`` unset and never creates a
    database (it collects but does not run tests).
    """
    base = os.environ.get("DB_TEST_NAME") or "tckdb_test"
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    safe_worker = _sanitize_identifier(worker) if worker else None

    suffix = f"_{RUN_TOKEN}"
    if safe_worker:
        suffix = f"{suffix}_{safe_worker}"
    if base.endswith(suffix):
        # Idempotent: ``db_engine`` exports the resolved name back into
        # ``DB_TEST_NAME``, so a second resolution in the same process (a
        # subprocess test, a fixture driven directly) must not stack a
        # second token onto a name that already carries one.
        return base
    return _with_run_suffix(base, suffix)


# Keep the boot-time dependency probes out of the suite.
#
# ``create_app()`` runs once per ``client`` fixture, i.e. hundreds of times.
# The storage probe is a real network round trip with a 4-second ceiling --
# on a machine with no MinIO that is hours, and with MinIO it is hundreds of
# pointless ``head_bucket`` calls -- and the encoding probe would re-answer
# one unchanging question about one cluster on every one of them. The probes'
# own behaviour, including that they are on by default, is covered directly
# in ``tests/api/test_startup_probes.py``.
#
# Set at import rather than by an autouse session fixture. A session-scoped
# autouse fixture in the root conftest joins the session fixture graph ahead
# of ``db_engine`` without depending on it, and under xdist that reordering
# was enough to have the per-worker database torn down while tests were still
# using it: 2000+ errors, all of them `terminating connection due to
# administrator command`, none of them near this line. An env var wants no
# fixture, so it should not have one.
#
# ``setdefault`` so a developer can still force the probes on from the
# environment when working on them.
os.environ.setdefault("TCKDB_STARTUP_PROBES", "false")

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
# The ambient session factory
#
# ``app.api.deps`` builds ``engine``/``SessionLocal`` at import time from
# ``settings.database_url``, i.e. from the ambient ``DB_NAME`` -- locally
# ``tckdb_dev``, and never the per-worker database this file creates.  Code
# that reaches for that factory during a test is therefore talking to a
# different database than every fixture and every assertion.
#
# That is not hypothetical.  It was root cause 2 of the seed-independence work
# (#64): five ``/status`` tests probed through ``health.SessionLocal``, passed
# in a dev shell and on the PR gate -- which runs ``alembic upgrade head``
# against ``DB_NAME`` in an earlier step -- and failed only on the nightly,
# which does not.  They were green for a reason unrelated to what they
# asserted.  The live hazard is worse than a false green: several of these call
# sites *commit*, and a commit into ``tckdb_dev`` is invisible to the
# committed-row tripwire below and to every assertion in the suite.
#
# Two bindings, in order:
#
# 1. At import, before any fixture runs, the factory is pointed at an engine
#    that refuses to connect.  A code path that needs an out-of-request session
#    and does not go through ``db_engine`` then *says so*, instead of quietly
#    writing somewhere nobody is looking.
# 2. ``db_engine`` rebinds it to the real per-worker engine for the duration of
#    the session, and puts the refusing engine back at teardown.
#
# ``sessionmaker.configure`` mutates the factory in place, so the modules that
# hold a ``from app.api.deps import SessionLocal`` reference -- health,
# idempotency, the upload worker, the archive CLI -- all follow.
# ---------------------------------------------------------------------------

_AMBIENT_REFUSAL_MESSAGE = (
    "app.api.deps.SessionLocal was used during a test while it is not bound "
    "to the pytest database. It binds at import to the ambient DB_NAME "
    "(locally tckdb_dev), which no fixture creates, migrates or inspects -- a "
    "write through it would be invisible to every assertion in the suite. "
    "Take the db_engine fixture (directly or via client/db_conn/db_session), "
    "or pass an explicit session_factory= to the service being tested."
)


def _refusing_ambient_engine():
    """An Engine that raises instead of connecting to the ambient database."""

    def _refuse():
        raise RuntimeError(_AMBIENT_REFUSAL_MESSAGE)

    return create_engine(
        "postgresql+psycopg://", creator=_refuse, poolclass=NullPool, future=True
    )


_AMBIENT_REFUSING_ENGINE = _refusing_ambient_engine()
api_deps.bind_ambient_session_factory(_AMBIENT_REFUSING_ENGINE)


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
#: ``run=`` is optional so markers written before run tokens existed still
#: parse, and so the sweep keeps recognising them as its own.
_MARKER_PATTERN = re.compile(
    rf"^{re.escape(_MARKER_PREFIX)} host=(\S+) pid=(\d+)(?: run=([A-Za-z0-9]+))?$"
)


def _safe_host() -> str:
    """Hostname reduced to characters safe inside a SQL string literal."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", socket.gethostname()) or "unknown"


def _ownership_marker() -> str:
    return f"{_MARKER_PREFIX} host={_safe_host()} pid={os.getpid()} run={RUN_TOKEN}"


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

    A marker carrying *this run's* token is never reclaimable, whatever its
    pid says.  Under ``-n`` the eight workers of one run share a token, and a
    worker whose creator pid has somehow been recycled must not have a sibling
    worker's live database dropped underneath it.
    """
    if not marker:
        return False
    match = _MARKER_PATTERN.match(marker.strip())
    if match is None:
        return False
    host, pid, run = match.group(1), match.group(2), match.group(3)
    if host != _safe_host():
        return False
    if run is not None and run == RUN_TOKEN:
        return False
    return not _pid_is_running(int(pid))


class ForeignTestDatabaseError(RuntimeError):
    """Raised when the database this run wants is owned by another run.

    Loud refusal is the whole point.  The failure this replaces was 2305
    errors reading ``terminating connection due to administrator command``,
    with nothing anywhere naming the real cause.
    """


def _refuse_foreign_test_database(connection, db_name: str) -> None:
    """Refuse to drop a database another pytest run is using.

    Run tokens make a collision vanishingly unlikely; this is the backstop
    that turns "unlikely" into "reported".  Two signals, either sufficient:

    * a **live backend** on the exact name we are about to recreate.  Within
      one run each worker owns a distinct name and creates it once, so a
      connection already attached to it belongs to somebody else;
    * an **ownership marker from a different run** on this host whose creator
      pid is still running — a run that has created its database but not yet
      connected to it, which the live-backend check alone would miss.

    An unmarked database is *not* refused: it was not created by this harness,
    the name pattern has already been validated, and dropping it is the
    documented behaviour of an explicit ``DB_TEST_NAME``.
    """
    row = connection.execute(
        text("""
            SELECT shobj_description(d.oid, 'pg_database') AS marker,
                   (
                       SELECT count(*) FROM pg_stat_activity a
                       WHERE a.datname = d.datname
                   ) AS backends
            FROM pg_database d
            WHERE d.datname = :db_name
        """),
        {"db_name": db_name},
    ).one_or_none()
    if row is None:
        return

    marker, backends = row.marker, int(row.backends or 0)
    reason: str | None = None
    if backends > 0:
        reason = f"{backends} live backend(s) are attached to it"
    else:
        match = _MARKER_PATTERN.match((marker or "").strip())
        if (
            match is not None
            and match.group(1) == _safe_host()
            and match.group(3) is not None
            and match.group(3) != RUN_TOKEN
            and _pid_is_running(int(match.group(2)))
        ):
            reason = (
                f"it is owned by pytest run {match.group(3)} "
                f"(pid {match.group(2)}), which is still running"
            )
    if reason is None:
        return

    raise ForeignTestDatabaseError(
        f"refusing to drop and recreate test database {db_name!r}: {reason}. "
        f"This run is {RUN_TOKEN}. Another pytest run on this host is using "
        "this database; dropping it would destroy that run and produce "
        "thousands of unrelated-looking connection errors in both. Give this "
        "run its own DB_TEST_NAME, or wait for the other one to finish."
    )


#: Scratch-database names built by individual tests must be reclaimable by
#: ``_sweep_stale_test_databases`` and by
#: ``backend/scripts/dev/reclaim_leaked_test_databases.py``, both of which only
#: ever look at ``tckdb_test%``.  Migration tests that named their scratch
#: databases ``tckdb_et_scope_*`` / ``tckdb_stage2_*`` leaked them permanently
#: whenever a run was killed partway.
_SCRATCH_PREFIX = "tckdb_test_"


def scratch_database_name(label: str) -> str:
    """Return a unique, reclaimable name for a test-owned scratch database.

    Every test that issues ``CREATE DATABASE`` must get its name from here.
    That is enforced by ``tests/test_scratch_database_names.py``, so a new
    migration test cannot quietly drift back outside the reclaimers' reach.

    ``label`` is a short human tag (``stage2_legacy``, ``et_scope_downgrade``)
    that survives into the name for diagnosis.  The uniqueness comes from a
    uuid4 hex, and the whole thing is trimmed to Postgres's 63-byte identifier
    limit **from the label end**, so the random part is never the thing
    truncation eats.
    """
    safe_label = _sanitize_identifier(label).strip("_")
    if not safe_label:
        raise ValueError("scratch database label must contain identifier characters")
    unique = uuid4().hex
    budget = _MAX_IDENTIFIER_BYTES - len(_SCRATCH_PREFIX) - len(unique) - 1
    if budget < 1:  # pragma: no cover - constant arithmetic, kept honest
        raise ValueError("scratch database name budget exhausted")
    name = f"{_SCRATCH_PREFIX}{safe_label[:budget]}_{unique}"
    return _validate_test_db_name(name)


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
            # Before the first destructive statement, not after: the whole
            # point is that another run's schema is never dropped.
            _refuse_foreign_test_database(connection, db_name)
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
        # Everything that cannot be handed a request-scoped session -- the
        # commit-time upload audit, the artifact-integrity event writer, the
        # upload worker, the idempotency decorator, the health probes -- now
        # reaches *this* database instead of the ambient one.
        api_deps.bind_ambient_session_factory(engine)
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
            # Put the refusing engine back first: the disposal and the drop
            # below both make this engine unusable, and anything reaching for
            # the ambient factory afterwards should hear why rather than
            # discover it through a dead connection.
            lambda: api_deps.bind_ambient_session_factory(_AMBIENT_REFUSING_ENGINE),
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


#: The watched counts as of the end of the last test that took them, and the
#: nodeid of that test.  Together they close the tripwire's one blind spot:
#: the fixture below can only see what happens *between its own two reads*, so
#: a write that lands anywhere else — the setup of a session- or module-scoped
#: fixture, which pytest instantiates before any function-scoped autouse
#: fixture; a subprocess; an ``atexit`` handler; a background thread — is
#: invisible to it, and surfaces hundreds of tests later as an unqualified
#: query returning a row nothing in the file created.  Comparing this test's
#: baseline against the previous test's final count names the gap the rows
#: appeared in, which is the difference between a five-minute fix and a day of
#: bisecting.
_committed_baseline: dict[str, int] | None = None
_committed_baseline_owner: str | None = None


def _record_committed_baseline(counts: dict[str, int], nodeid: str) -> None:
    global _committed_baseline, _committed_baseline_owner
    _committed_baseline = counts
    _committed_baseline_owner = nodeid


@pytest.fixture(autouse=True)
def _isolate_out_of_request_sessions(request) -> Iterator[None]:
    """Give out-of-request writers a connection that is rolled back at teardown.

    Some app code cannot be handed the request's session and must open its
    own — the durable failed-upload audit
    (``app.services.upload_submission.record_failed_upload``) is the one that
    fires most: every 4xx from a ``/uploads/*`` route records a ``submission``
    plus two audit events **in a transaction deliberately independent of the
    request's**, which has already rolled back by the time it runs. That
    independence is the feature; it is also why the request's per-test
    rollback cannot undo the write.

    Until the ambient factory was rebound to the pytest database, none of
    this landed anywhere the suite could see: it went to the ambient
    ``DB_NAME``, where the ``created_by`` foreign key has no matching
    ``app_user`` row, so every audit insert failed and was swallowed as
    best-effort. Twenty-one API tests drove that path and asserted only their
    status code. Once the binding tells the truth they commit for real, and
    the tripwire below reports them — correctly, because a committed
    ``submission`` row *is* visible to every later test.

    So the ambient factory gets a connection of its own, with the same
    outer-transaction-plus-SAVEPOINT shape ``db_conn`` uses:

    * a **different connection** from the request's, so the request's
      rollback does not reach it — the property the audit exists to have,
      and the one ``tests/services/test_upload_audit_isolation.py`` pins;
    * its own outer transaction, rolled back here, so nothing is ever
      committed and the tripwire has nothing to report.

    Autouse rather than part of ``client`` on purpose: bespoke client
    fixtures exist (``committed_api_client`` in
    ``tests/api/test_api_idempotency_receipt_isolation.py``), and one that
    forgot this would reintroduce the leak silently. A test that reaches no
    database fixture pays nothing, and a test that wants the write visible
    from a third connection still binds its own factory, which wins.
    """
    if _DB_FIXTURE_NAMES.isdisjoint(request.fixturenames):
        yield
        return

    engine = request.getfixturevalue("db_engine")
    connection = engine.connect()
    transaction = connection.begin()
    # An open SAVEPOINT makes SQLAlchemy resolve each Session's
    # ``join_transaction_mode`` to ``create_savepoint``, so a writer's commit
    # stays inside this transaction instead of ending it.
    connection.begin_nested()
    # Hold the factory object, not the module attribute: a test that
    # monkeypatches ``api_deps.SessionLocal`` outright must have its own
    # object restored by monkeypatch, and this teardown must put the bind
    # back on the object it took it from.
    factory = api_deps.SessionLocal
    previous_bind = factory.kw.get("bind")
    factory.configure(bind=connection)
    try:
        yield
    finally:
        factory.configure(bind=previous_bind)
        transaction.rollback()
        connection.close()


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

    Two checks, not one. The second compares this test's baseline against the
    previous test's final count: rows that appeared in between were written by
    something that is not a test body at all, which is the one leak the
    original check cannot see (see ``_committed_baseline``).

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

    # Rows that appeared since the previous test finished belong to neither
    # test's body.  Report the gap and re-baseline, so the next test is not
    # blamed for the same rows a second time.
    drifted = (
        {}
        if _committed_baseline is None
        else {
            table: (_committed_baseline[table], before[table])
            for table in before
            if table in _committed_baseline and before[table] != _committed_baseline[table]
        }
    )
    if drifted:
        previous = _committed_baseline_owner
        _record_committed_baseline(before, request.node.nodeid)
        raise AssertionError(
            "the shared test database changed between "
            f"{previous} and {request.node.nodeid}, outside either test body: "
            + ", ".join(f"{t} {was}->{now}" for t, (was, now) in sorted(drifted.items()))
            + ". Nothing between those two tests runs inside the per-test "
            "rollback, so the writer is a session- or module-scoped fixture, a "
            "subprocess, or a background thread. Give it a teardown that undoes "
            "exactly what it did."
        )

    yield

    after = _committed_counts(probe)
    _record_committed_baseline(after, request.node.nodeid)
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


@pytest.fixture
def client(db_engine, _api_test_user) -> Iterator[TestClient]:
    """TestClient with per-test transaction rollback.

    The session is bound to a connection with an open transaction that is
    rolled back after the test, so no data persists between tests.

    Out-of-request writers (the failed-upload audit, above all) do not use
    this session — see ``_isolate_out_of_request_sessions``.
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
