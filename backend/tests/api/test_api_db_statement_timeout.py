"""Tests for F13 -- the PostgreSQL ``statement_timeout`` on application engines.

The timeout must hold for the whole life of a pooled connection, not only its
first checkout (#604). Before, it was a ``SET`` in a ``connect`` listener,
issued inside psycopg's implicit transaction and undone by the pool's
rollback-on-return: measured on a deployed instance as ``['30s', '0', '0']``
over three checkouts, so the guard covered the first query per connection and
nothing after it. Every check here therefore looks at a *later* checkout of a
connection it has proved is the same one, and cancels a real ``pg_sleep``
rather than only reading the setting back.

Also here: the :class:`OperationalError` handler maps SQLSTATE 57014
(``query_canceled``) to a sanitized 503 ``query_timeout`` response without
leaking the offending SQL, exercised by simulating the wrapped driver
exception directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api import deps as api_deps
from app.api.config import settings
from app.api.deps import create_app_engine, statement_timeout_connect_args
from app.api.errors import register_exception_handlers

BACKEND_DIR = Path(__file__).resolve().parents[2]

QUERY_CANCELED = "57014"

# ---------------------------------------------------------------------------
# One pooled connection, checked out again and again
# ---------------------------------------------------------------------------


def _single_connection_engine(db_engine, timeout_ms):
    """An application engine whose pool holds exactly one connection.

    ``pool_size=1, max_overflow=0`` is what makes "the same connection, three
    times" a fact rather than a hope; :func:`_checkout` records the DBAPI
    connection's identity so each test asserts it.
    """
    return create_app_engine(db_engine.url, statement_timeout_ms=timeout_ms, pool_size=1, max_overflow=0)


def _checkout(eng, n, fn):
    """Run ``fn(connection)`` on *n* successive checkouts; return results and DBAPI ids."""
    results, ids = [], []
    for _ in range(n):
        with eng.connect() as connection:
            ids.append(id(connection.connection.dbapi_connection))
            results.append(fn(connection))
    return results, ids


def _show(connection) -> str:
    return connection.execute(text("SHOW statement_timeout")).scalar()


def _server_default(db_engine) -> str:
    """What a plain connection with no startup option reports (role/server default)."""
    plain = create_engine(db_engine.url)
    try:
        with plain.connect() as connection:
            return _show(connection)
    finally:
        plain.dispose()


def test_successive_checkouts_of_one_connection_all_report_the_configured_timeout(db_engine):
    eng = _single_connection_engine(db_engine, 15_000)
    try:
        shown, ids = _checkout(eng, 4, _show)
    finally:
        eng.dispose()
    assert len(set(ids)) == 1, "the checkouts must reuse one DBAPI connection or this proves nothing"
    # PostgreSQL normalizes 15000 ms to "15s". The old listener gave
    # ['15s', '0', '0', '0'].
    assert shown == ["15s"] * 4


def test_timeout_survives_an_explicit_rollback_and_a_commit(db_engine):
    eng = _single_connection_engine(db_engine, 15_000)
    try:
        with eng.connect() as connection:
            assert _show(connection) == "15s"
            connection.rollback()
            assert _show(connection) == "15s"
            connection.commit()
            assert _show(connection) == "15s"
        with eng.connect() as connection:
            assert _show(connection) == "15s"
    finally:
        eng.dispose()


def test_a_slow_statement_is_cancelled_on_the_second_and_third_checkout(db_engine):
    eng = _single_connection_engine(db_engine, 200)

    def _sleep_two_seconds(connection):
        started = time.monotonic()
        with pytest.raises(OperationalError) as caught:
            connection.execute(text("SELECT pg_sleep(2)"))
        return getattr(caught.value.orig, "sqlstate", None), time.monotonic() - started

    try:
        outcomes, ids = _checkout(eng, 3, _sleep_two_seconds)
    finally:
        eng.dispose()
    assert len(set(ids)) == 1
    for sqlstate, elapsed in outcomes:
        assert sqlstate == QUERY_CANCELED
        # Cancelled near the 200 ms limit, nowhere near the 2 s the sleep asked for.
        assert elapsed < 1.5


def test_a_statement_inside_the_limit_still_completes_on_a_later_checkout(db_engine):
    eng = _single_connection_engine(db_engine, 5_000)
    try:
        values, _ = _checkout(eng, 3, lambda c: c.execute(text("SELECT pg_sleep(0.05), 1")).one()[1])
    finally:
        eng.dispose()
    assert values == [1, 1, 1]


def test_zero_none_and_negative_mean_no_app_level_timeout(db_engine):
    baseline = _server_default(db_engine)
    for disabled in (0, None, -1):
        assert statement_timeout_connect_args(disabled) == {}
        eng = _single_connection_engine(db_engine, disabled)
        try:
            shown, _ = _checkout(eng, 3, _show)
            # A statement that a small limit would cancel runs to the end.
            slept, _ = _checkout(eng, 2, lambda c: c.execute(text("SELECT pg_sleep(0.4), 7")).one()[1])
        finally:
            eng.dispose()
        assert shown == [baseline] * 3
        assert slept == [7, 7]


def test_the_configured_setting_is_the_default_and_an_explicit_value_overrides_it(db_engine, monkeypatch):
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 7_000)
    eng = create_app_engine(db_engine.url, pool_size=1, max_overflow=0)
    try:
        assert _checkout(eng, 3, _show)[0] == ["7s"] * 3
    finally:
        eng.dispose()

    monkeypatch.setattr(settings, "db_statement_timeout_ms", 0)
    eng = create_app_engine(db_engine.url, pool_size=1, max_overflow=0)
    try:
        assert _checkout(eng, 3, _show)[0] == [_server_default(db_engine)] * 3
    finally:
        eng.dispose()

    eng = create_app_engine(db_engine.url, statement_timeout_ms=9_000, pool_size=1, max_overflow=0)
    try:
        assert _checkout(eng, 3, _show)[0] == ["9s"] * 3
    finally:
        eng.dispose()


def test_caller_supplied_startup_options_are_kept(db_engine):
    eng = create_app_engine(
        db_engine.url,
        statement_timeout_ms=15_000,
        connect_args={"options": "-c lock_timeout=4321"},
        pool_size=1,
        max_overflow=0,
    )
    try:
        shown, _ = _checkout(eng, 2, lambda c: (_show(c), c.execute(text("SHOW lock_timeout")).scalar()))
    finally:
        eng.dispose()
    assert shown == [("15s", "4321ms")] * 2


# ---------------------------------------------------------------------------
# The engines the application and the suite really use
# ---------------------------------------------------------------------------


def test_the_harness_engine_runs_with_the_configured_timeout(db_engine):
    """The suite itself now runs with the timeout on, on every checkout."""
    if not settings.db_statement_timeout_ms:
        pytest.skip("DB_STATEMENT_TIMEOUT_MS is disabled in this environment")
    shown = []
    for _ in range(3):
        with Session(db_engine) as session:
            shown.append(session.execute(text("SHOW statement_timeout")).scalar())
            session.rollback()
    expected = "30s" if settings.db_statement_timeout_ms == 30_000 else shown[0]
    assert shown == [expected] * 3
    assert shown[0] != _server_default(db_engine)


def test_the_stamp_engine_has_the_timeout_on_every_checkout(db_engine):
    """One mechanism: the stamp engine gets it from the engine it derives from."""
    if not settings.db_statement_timeout_ms:
        pytest.skip("DB_STATEMENT_TIMEOUT_MS is disabled in this environment")
    stamp = api_deps.stamp_engine
    assert stamp is not db_engine
    shown, ids = _checkout(stamp, 3, _show)
    assert len(set(ids)) == 1, "the stamp pool must hand back one connection for this to prove anything"
    assert shown[0] != _server_default(db_engine)
    assert len(set(shown)) == 1


def test_a_stamp_engine_derived_from_an_engine_without_the_option_has_no_timeout(db_engine):
    """The flip side, so the test above cannot pass by accident: nothing else sets it."""
    bare = create_app_engine(db_engine.url, statement_timeout_ms=0)
    derived = api_deps._derive_stamp_engine(bare)
    try:
        shown, _ = _checkout(derived, 2, _show)
    finally:
        derived.dispose()
        bare.dispose()
    assert shown == [_server_default(db_engine)] * 2


def test_the_real_module_engine_carries_the_timeout_on_three_checkouts(db_engine):
    """``app.api.deps.engine`` as built at import, in a process of its own.

    The suite rebinds the module engine to a harness engine, so it cannot be
    inspected in-process. A child process imports ``app.api.deps`` with a
    distinctive ``DB_STATEMENT_TIMEOUT_MS`` and reads the setting back over
    three checkouts of the pool.
    """
    url = db_engine.url
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "DB_USER": url.username,
        "DB_PASSWORD": url.password,
        "DB_HOST": url.host,
        "DB_PORT": str(url.port),
        "DB_NAME": url.database,
        "DB_STATEMENT_TIMEOUT_MS": "12345",
    }
    code = (
        "from sqlalchemy import text\n"
        "from app.api import deps\n"
        "out = []\n"
        "for _ in range(3):\n"
        "    with deps.engine.connect() as c:\n"
        "        out.append(c.execute(text('SHOW statement_timeout')).scalar())\n"
        "print('RESULT', out)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert "RESULT ['12345ms', '12345ms', '12345ms']" in done.stdout, done.stdout


def test_alembic_gets_no_statement_timeout():
    """Migrations may legitimately run one statement for a long time.

    ``alembic/env.py`` builds its own ``NullPool`` engine from ``sqlalchemy.url``
    and never touches ``app.api.deps``, so none of the machinery above applies
    to it. This pins that: routing it through ``create_app_engine`` (or the
    setting) would put the request-time limit on an index build.
    """
    source = (BACKEND_DIR / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "app.api.deps" not in source
    assert "create_app_engine" not in source
    assert "statement_timeout" not in source


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
