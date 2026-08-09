"""Regression tests for the test-database lifecycle in ``conftest``.

The session ``db_engine`` fixture creates a throwaway PostgreSQL database
under a name that is never reused (``tckdb_test_<pid>`` by default).  If any
step between creation and teardown escapes the fixture's ``finally``, the
database survives the run forever.  That leak once accumulated ~900 orphaned
databases (12 GB) on the local dev server, all of them fully migrated.

These tests pin the two guarantees that close it:

1. ``db_engine`` drops its database on *failure* paths, not only on a clean
   session exit (``test_db_engine_drops_database_when_migrations_fail``).
2. The startup sweep reclaims only databases this harness demonstrably
   abandoned, and leaves everything else — unmarked databases, databases
   belonging to a live pytest process — untouched.

They talk to the real local PostgreSQL, but only ever create and destroy
databases they named themselves.
"""

from __future__ import annotations

import os
import subprocess
import warnings

import conftest
import pytest
from sqlalchemy import create_engine, text


def _admin_engine():
    return create_engine(
        conftest._database_url("postgres"), future=True, isolation_level="AUTOCOMMIT"
    )


def _database_exists(db_name: str) -> bool:
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            return (
                connection.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
                ).scalar()
                is not None
            )
    finally:
        engine.dispose()


def _force_drop(db_name: str) -> None:
    """Unconditional cleanup for databases these tests created themselves."""
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    except Exception:
        pass
    finally:
        engine.dispose()


def _create_marked(db_name: str, marker: str | None) -> None:
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            connection.execute(text(f'CREATE DATABASE "{db_name}"'))
            if marker is not None:
                connection.execute(text(f"COMMENT ON DATABASE \"{db_name}\" IS '{marker}'"))
    finally:
        engine.dispose()


def _db_engine_generator():
    """Drive the ``db_engine`` fixture body directly, outside pytest's runner.

    ``@pytest.fixture`` wraps the generator function; ``__wrapped__`` is the
    standard escape hatch for reaching it. The ``getattr`` fallback keeps this
    working if a future pytest stops wrapping.
    """
    factory = getattr(conftest.db_engine, "__wrapped__", conftest.db_engine)
    return factory()


def _dead_pid() -> int:
    """A pid that has certainly exited: fork a trivial child and reap it."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


@pytest.fixture(autouse=True)
def _name_the_database_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let these tests choose the database name they then assert about.

    ``_resolve_test_db_name`` appends the xdist worker id to an explicit
    ``DB_TEST_NAME`` so that ``-n 8`` gives eight databases rather than eight
    workers fighting over one. The tests below drive the ``db_engine`` fixture
    body directly and check for a database under the exact name they set, so
    under xdist they would look for ``tckdb_test_regress_1234`` while the
    fixture created ``tckdb_test_regress_1234_gw3``.

    Clearing the worker id is the honest fix: what these tests pin is the
    *lifecycle* (create, migrate, drop on every failure path), not the naming —
    which ``tests/test_db_name_resolution.py`` covers on its own.
    """
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


# ---------------------------------------------------------------------------
# 1. The leak itself
# ---------------------------------------------------------------------------


def test_db_engine_drops_database_when_migrations_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing ``alembic upgrade`` must not leave the database behind.

    This is the exact shape of the historical leak: ``_recreate_test_database``
    had already run, so the database existed, but the migration subprocess
    raised *before* the fixture entered its ``try`` — so ``finally`` never ran.

    Against the pre-fix fixture this test fails on the final assertion (the
    database survives); against the fixed fixture it passes.
    """
    db_name = f"tckdb_test_regress_{os.getpid()}"
    monkeypatch.setenv("DB_TEST_NAME", db_name)
    # Keep this unit test off the sweep path entirely.
    monkeypatch.setenv("TCKDB_TEST_DB_SWEEP", "0")

    existed_at_failure: list[bool] = []

    def _failing_migration(*args: object, **kwargs: object) -> None:
        # Proves the assertion below is not vacuous: the database really was
        # created before the failure, so a surviving database is a true leak.
        existed_at_failure.append(_database_exists(db_name))
        raise subprocess.CalledProcessError(1, "alembic upgrade head")

    monkeypatch.setattr(conftest.subprocess, "run", _failing_migration)

    try:
        generator = _db_engine_generator()
        with pytest.raises(subprocess.CalledProcessError):
            next(generator)

        assert existed_at_failure == [True], "fixture never created the database"
        assert not _database_exists(db_name), (
            "db_engine leaked its database when the migration step failed"
        )
    finally:
        _force_drop(db_name)


def test_db_engine_drops_database_when_session_body_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception thrown into the fixture must also drop the database."""
    db_name = f"tckdb_test_regress_throw_{os.getpid()}"
    monkeypatch.setenv("DB_TEST_NAME", db_name)
    monkeypatch.setenv("TCKDB_TEST_DB_SWEEP", "0")

    def _noop_migration(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(conftest.subprocess, "run", _noop_migration)

    try:
        generator = _db_engine_generator()
        next(generator)
        assert _database_exists(db_name)

        with pytest.raises(RuntimeError):
            generator.throw(RuntimeError("collection error"))

        assert not _database_exists(db_name), (
            "db_engine leaked its database when the session raised"
        )
    finally:
        _force_drop(db_name)


# ---------------------------------------------------------------------------
# 2. The startup sweep
# ---------------------------------------------------------------------------


def test_sweep_reclaims_abandoned_marked_database() -> None:
    """A marked database whose creator pid is gone is reclaimed."""
    db_name = f"tckdb_test_sweepdead_{os.getpid()}"
    marker = f"{conftest._MARKER_PREFIX} host={conftest._safe_host()} pid={_dead_pid()}"
    _create_marked(db_name, marker)
    try:
        assert _database_exists(db_name)
        conftest._sweep_stale_test_databases("tckdb_test_not_this_one")
        assert not _database_exists(db_name)
    finally:
        _force_drop(db_name)


def test_sweep_ignores_unmarked_database() -> None:
    """Databases without this harness's marker are never touched.

    This is what protects pre-existing orphans (and any database created by
    another tool that happens to match the name pattern) from being deleted
    without a human deciding to.
    """
    db_name = f"tckdb_test_sweepunmarked_{os.getpid()}"
    _create_marked(db_name, marker=None)
    try:
        conftest._sweep_stale_test_databases("tckdb_test_not_this_one")
        assert _database_exists(db_name), "sweep deleted an unmarked database"
    finally:
        _force_drop(db_name)


def test_sweep_ignores_database_owned_by_a_live_process() -> None:
    """A concurrent pytest run that has created but not yet connected to its
    database must not have it swept out from under it."""
    db_name = f"tckdb_test_sweeplive_{os.getpid()}"
    _create_marked(db_name, conftest._ownership_marker())  # our own, very much alive
    try:
        conftest._sweep_stale_test_databases("tckdb_test_not_this_one")
        assert _database_exists(db_name), "sweep deleted a live process's database"
    finally:
        _force_drop(db_name)


def test_sweep_ignores_foreign_host_marker() -> None:
    """Markers written on another host carry pids we cannot reason about."""
    db_name = f"tckdb_test_sweepforeign_{os.getpid()}"
    marker = f"{conftest._MARKER_PREFIX} host=some-other-box pid={_dead_pid()}"
    _create_marked(db_name, marker)
    try:
        conftest._sweep_stale_test_databases("tckdb_test_not_this_one")
        assert _database_exists(db_name), "sweep trusted a foreign host's pid"
    finally:
        _force_drop(db_name)


def test_sweep_never_targets_the_current_session_database() -> None:
    db_name = f"tckdb_test_sweepcurrent_{os.getpid()}"
    marker = f"{conftest._MARKER_PREFIX} host={conftest._safe_host()} pid={_dead_pid()}"
    _create_marked(db_name, marker)
    try:
        conftest._sweep_stale_test_databases(db_name)
        assert _database_exists(db_name), "sweep dropped the in-use session database"
    finally:
        _force_drop(db_name)


def test_sweep_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    db_name = f"tckdb_test_sweepoff_{os.getpid()}"
    marker = f"{conftest._MARKER_PREFIX} host={conftest._safe_host()} pid={_dead_pid()}"
    _create_marked(db_name, marker)
    monkeypatch.setenv("TCKDB_TEST_DB_SWEEP", "0")
    try:
        conftest._sweep_stale_test_databases("tckdb_test_not_this_one")
        assert _database_exists(db_name)
    finally:
        _force_drop(db_name)


def test_recreate_stamps_ownership_marker() -> None:
    """The marker the sweep depends on is actually written at creation."""
    db_name = f"tckdb_test_marker_{os.getpid()}"
    try:
        conftest._recreate_test_database(db_name)
        engine = _admin_engine()
        try:
            with engine.connect() as connection:
                marker = connection.execute(
                    text(
                        "SELECT shobj_description(oid, 'pg_database') "
                        "FROM pg_database WHERE datname = :n"
                    ),
                    {"n": db_name},
                ).scalar()
        finally:
            engine.dispose()

        assert marker is not None
        assert conftest._MARKER_PATTERN.match(marker)
        assert f"pid={os.getpid()}" in marker
    finally:
        _force_drop(db_name)


def test_cleanup_failure_does_not_mask_the_body_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed drop must not bury the failed ``alembic upgrade``.

    Python replaces a propagating exception with anything raised from
    ``finally``. Before migrations moved inside the ``try`` this path did not
    exist; now it does, so a secondary "could not drop" error could hide the
    migration failure that is the thing actually worth reading.

    Against a fixture whose ``finally`` calls cleanup unguarded, this test
    fails with ``RuntimeError`` instead of ``CalledProcessError``.
    """
    db_name = f"tckdb_test_mask_{os.getpid()}"
    monkeypatch.setenv("DB_TEST_NAME", db_name)
    monkeypatch.setenv("TCKDB_TEST_DB_SWEEP", "0")

    monkeypatch.setattr(conftest, "_recreate_test_database", lambda _name: None)

    def _failing_migration(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "alembic upgrade head")

    def _failing_drop(*args: object, **kwargs: object) -> None:
        raise RuntimeError("could not reach postgres to drop")

    monkeypatch.setattr(conftest.subprocess, "run", _failing_migration)
    monkeypatch.setattr(conftest, "_drop_test_database", _failing_drop)

    generator = conftest.db_engine.__wrapped__()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # The migration error is what surfaces, not the cleanup error.
        with pytest.raises(subprocess.CalledProcessError):
            next(generator)

    # The leak is still reported rather than silently swallowed.
    assert any(
        "cleanup failed" in str(w.message) and db_name in str(w.message)
        for w in caught
    ), f"cleanup failure was swallowed silently: {[str(w.message) for w in caught]}"
