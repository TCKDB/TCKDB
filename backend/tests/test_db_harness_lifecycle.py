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
from conftest import scratch_database_name
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


@pytest.fixture(autouse=True)
def _restore_ambient_session_binding():
    """Undo the fixture body's rebinding of ``app.api.deps.SessionLocal``.

    ``db_engine`` binds the ambient session factory to its engine and puts
    the refusing engine back in its ``finally``. The tests below drive that
    body directly, outside pytest's fixture runner, so its teardown would
    leave the ambient factory refusing for every *later* test in this worker
    — a session-scoped side effect escaping a function-scoped test.
    """
    from app.api import deps as api_deps

    previous = api_deps.engine
    try:
        yield
    finally:
        api_deps.bind_ambient_session_factory(previous)


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
    monkeypatch.setenv("DB_TEST_NAME", scratch_database_name("regress"))
    db_name = conftest._resolve_test_db_name()
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
    monkeypatch.setenv("DB_TEST_NAME", scratch_database_name("regress_throw"))
    db_name = conftest._resolve_test_db_name()
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
    db_name = scratch_database_name("sweepdead")
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
    db_name = scratch_database_name("sweepunmarked")
    _create_marked(db_name, marker=None)
    try:
        conftest._sweep_stale_test_databases("tckdb_test_not_this_one")
        assert _database_exists(db_name), "sweep deleted an unmarked database"
    finally:
        _force_drop(db_name)


def test_sweep_ignores_database_owned_by_a_live_process() -> None:
    """A concurrent pytest run that has created but not yet connected to its
    database must not have it swept out from under it."""
    db_name = scratch_database_name("sweeplive")
    _create_marked(db_name, conftest._ownership_marker())  # our own, very much alive
    try:
        conftest._sweep_stale_test_databases("tckdb_test_not_this_one")
        assert _database_exists(db_name), "sweep deleted a live process's database"
    finally:
        _force_drop(db_name)


def test_sweep_ignores_foreign_host_marker() -> None:
    """Markers written on another host carry pids we cannot reason about."""
    db_name = scratch_database_name("sweepforeign")
    marker = f"{conftest._MARKER_PREFIX} host=some-other-box pid={_dead_pid()}"
    _create_marked(db_name, marker)
    try:
        conftest._sweep_stale_test_databases("tckdb_test_not_this_one")
        assert _database_exists(db_name), "sweep trusted a foreign host's pid"
    finally:
        _force_drop(db_name)


def test_sweep_never_targets_the_current_session_database() -> None:
    db_name = scratch_database_name("sweepcurrent")
    marker = f"{conftest._MARKER_PREFIX} host={conftest._safe_host()} pid={_dead_pid()}"
    _create_marked(db_name, marker)
    try:
        conftest._sweep_stale_test_databases(db_name)
        assert _database_exists(db_name), "sweep dropped the in-use session database"
    finally:
        _force_drop(db_name)


def test_sweep_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    db_name = scratch_database_name("sweepoff")
    marker = f"{conftest._MARKER_PREFIX} host={conftest._safe_host()} pid={_dead_pid()}"
    _create_marked(db_name, marker)
    monkeypatch.setenv("TCKDB_TEST_DB_SWEEP", "0")
    try:
        conftest._sweep_stale_test_databases("tckdb_test_not_this_one")
        assert _database_exists(db_name)
    finally:
        _force_drop(db_name)


# ---------------------------------------------------------------------------
# 3. Refusing another run's database
#
# Run tokens make two concurrent runs pick different names, so the refusal
# below is a backstop rather than the primary defence. It exists because the
# failure it replaces was unreadable: on 2026-08-10 two runs sharing eight
# databases produced 2305 errors, every one of them reading `terminating
# connection due to administrator command`, and nothing anywhere said "another
# run is using your database".
# ---------------------------------------------------------------------------


def test_recreate_refuses_a_database_owned_by_another_live_run() -> None:
    """A marker from a different, still-running run stops the drop."""
    db_name = scratch_database_name("foreignrun")
    marker = (
        f"{conftest._MARKER_PREFIX} host={conftest._safe_host()} "
        f"pid={os.getpid()} run=deadbeef"
    )
    _create_marked(db_name, marker)
    try:
        with pytest.raises(conftest.ForeignTestDatabaseError) as excinfo:
            conftest._recreate_test_database(db_name)

        assert "deadbeef" in str(excinfo.value)
        assert _database_exists(db_name), "refusal still dropped the database"
    finally:
        _force_drop(db_name)


def test_recreate_refuses_a_database_with_a_live_backend() -> None:
    """A connection already attached to the name is somebody else's.

    Within one run each worker owns a distinct name and creates it exactly
    once, so this can only be a foreign run — including one whose marker this
    harness cannot read (an older harness, or a different host's).
    """
    db_name = scratch_database_name("foreignbackend")
    _create_marked(db_name, marker=None)
    engine = create_engine(conftest._database_url(db_name), future=True)
    try:
        with engine.connect() as held:
            held.execute(text("SELECT 1"))
            with pytest.raises(conftest.ForeignTestDatabaseError) as excinfo:
                conftest._recreate_test_database(db_name)

        assert "live backend" in str(excinfo.value)
        assert _database_exists(db_name)
    finally:
        engine.dispose()
        _force_drop(db_name)


def test_drop_refuses_a_database_stamped_by_another_run() -> None:
    """Refusing to overwrite something and then deleting it is not a refusal.

    ``_drop_test_database`` terminates backends before it drops — it has to,
    because pytest's pool may not have released every connection — so it is
    the one statement in the harness that can destroy a *live* database.

    Found by forcing the collision the run token normally prevents: two runs
    pinned to one ``TCKDB_TEST_RUN_TOKEN``. The second refused to recreate,
    exactly as designed, and its fixture ``finally`` then dropped the first
    run's database anyway — 38 errors in a run that had done nothing wrong.
    """
    db_name = scratch_database_name("dropforeign")
    marker = (
        f"{conftest._MARKER_PREFIX} host={conftest._safe_host()} "
        f"pid={os.getpid()} run=deadbeef"
    )
    _create_marked(db_name, marker)
    try:
        with pytest.raises(conftest.ForeignTestDatabaseError):
            conftest._drop_test_database(db_name)

        assert _database_exists(db_name), "cleanup dropped another run's database"
    finally:
        _force_drop(db_name)


def test_drop_accepts_this_runs_own_database() -> None:
    """The refusal must not stop the harness cleaning up after itself."""
    db_name = scratch_database_name("dropown")
    try:
        conftest._recreate_test_database(db_name)
        assert _database_exists(db_name)

        conftest._drop_test_database(db_name)

        assert not _database_exists(db_name)
    finally:
        _force_drop(db_name)


def test_a_refused_session_does_not_drop_the_database_it_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the fixture body refuses, and cleanup leaves it alone.

    Drives the ``db_engine`` body directly against a database stamped by a
    live foreign run, which is what the forced-collision experiment produced.
    """
    monkeypatch.setenv("DB_TEST_NAME", scratch_database_name("refusedsession"))
    monkeypatch.setenv("TCKDB_TEST_DB_SWEEP", "0")
    db_name = conftest._resolve_test_db_name()
    marker = (
        f"{conftest._MARKER_PREFIX} host={conftest._safe_host()} "
        f"pid={os.getpid()} run=deadbeef"
    )
    _create_marked(db_name, marker)

    try:
        generator = _db_engine_generator()
        with pytest.raises(conftest.ForeignTestDatabaseError):
            next(generator)

        assert _database_exists(db_name), (
            "a refused session dropped the database it had just refused to touch"
        )
    finally:
        _force_drop(db_name)


def test_recreate_accepts_a_database_abandoned_by_a_dead_run() -> None:
    """An orphan from a crashed run is reclaimed, not refused.

    The refusal must not turn every leaked database into a permanent
    blocker — the whole reason names carry a run token is that leaks are
    expected and reclaimable.
    """
    db_name = scratch_database_name("deadrun")
    marker = (
        f"{conftest._MARKER_PREFIX} host={conftest._safe_host()} "
        f"pid={_dead_pid()} run=deadbeef"
    )
    _create_marked(db_name, marker)
    try:
        conftest._recreate_test_database(db_name)
        assert _database_exists(db_name)
    finally:
        _force_drop(db_name)


def test_two_runs_resolve_different_database_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same DB_TEST_NAME in two runs must not name one database.

    This is the shape that actually bit: two agents copy-pasting the same
    ``DB_TEST_NAME=...`` out of the same document, on one host.
    """
    monkeypatch.setenv("DB_TEST_NAME", "tckdb_test_shared_label")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")

    monkeypatch.setattr(conftest, "RUN_TOKEN", "aaaa1111")
    first = conftest._resolve_test_db_name()
    monkeypatch.setattr(conftest, "RUN_TOKEN", "bbbb2222")
    second = conftest._resolve_test_db_name()

    assert first != second
    assert first.endswith("_aaaa1111_gw3")
    assert second.endswith("_bbbb2222_gw3")


def test_recreate_stamps_ownership_marker() -> None:
    """The marker the sweep depends on is actually written at creation."""
    db_name = scratch_database_name("marker")
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
    db_name = scratch_database_name("mask")
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
