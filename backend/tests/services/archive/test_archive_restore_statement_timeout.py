"""A restore's statements run under their own ceiling, not the API's 30 s.

With the API's ``statement_timeout`` really applied to every checkout (#604),
a restore's statements would inherit it. ``restore_archive`` raises the limit
for its own savepoint and puts it back afterwards. The ceiling covers the
restore's statements, not its final ``COMMIT`` (PostgreSQL disarms the timer
before COMMIT), so what these tests prove is the behaviour that matters: a
restore statement slower than a small limit is cancelled without the helper
and completes with it.

Every test sets its own limit with ``SET LOCAL`` rather than assuming
``DB_STATEMENT_TIMEOUT_MS``, so none depends on the environment.
"""

from __future__ import annotations

import io
from contextlib import nullcontext

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.services.archive import core as archive_core
from app.services.archive import restore_archive, write_archive
from tests.services.archive.test_archive import _empty_archive_tables

SMALL_LIMIT_MS = 300
TEST_CEILING_MS = 20_000
SLOW_INSERT_SECONDS = 0.8


def _show_ms(session) -> int:
    return int(
        session.execute(text("SELECT setting::bigint FROM pg_settings WHERE name = 'statement_timeout'")).scalar_one()
    )


def _archive_then_limit(db_session, limit_ms: int) -> bytes:
    """Archive the (emptied) database, then apply *limit_ms* to this transaction."""
    _empty_archive_tables(db_session)
    payload = io.BytesIO()
    write_archive(db_session, payload)
    db_session.execute(text(f"SET LOCAL statement_timeout = {limit_ms}"))
    return payload.getvalue()


def _make_restore_inserts_slow(db_session) -> None:
    """Every INSERT into ``reaction_family`` (a seeded table) takes ~0.8 s.

    A statement-level trigger, created inside the test's transaction so it is
    rolled back with it.
    """
    db_session.execute(
        text(
            "CREATE FUNCTION pg_temp.slow_restore_insert() RETURNS trigger LANGUAGE plpgsql AS "
            f"$$ BEGIN PERFORM pg_sleep({SLOW_INSERT_SECONDS}); RETURN NULL; END $$"
        )
    )
    db_session.execute(
        text(
            "CREATE TRIGGER slow_restore_insert BEFORE INSERT ON reaction_family "
            "FOR EACH STATEMENT EXECUTE FUNCTION pg_temp.slow_restore_insert()"
        )
    )


@pytest.fixture
def small_ceiling(monkeypatch):
    monkeypatch.setattr(archive_core, "ARCHIVE_RESTORE_STATEMENT_TIMEOUT_MS", TEST_CEILING_MS)


def test_a_restore_insert_slower_than_the_limit_is_cancelled_without_the_helper(
    db_session, monkeypatch, small_ceiling
) -> None:
    """The control: this is the failure the helper prevents."""
    archive = _archive_then_limit(db_session, SMALL_LIMIT_MS)
    _make_restore_inserts_slow(db_session)
    monkeypatch.setattr(archive_core, "_long_restore_statements", lambda session: nullcontext())
    with pytest.raises(OperationalError) as caught:
        restore_archive(db_session, io.BytesIO(archive))
    assert getattr(caught.value.orig, "sqlstate", None) == "57014"


def test_the_same_restore_completes_with_the_helper(db_session, small_ceiling) -> None:
    archive = _archive_then_limit(db_session, SMALL_LIMIT_MS)
    _make_restore_inserts_slow(db_session)
    report = restore_archive(db_session, io.BytesIO(archive))
    assert report.rows_restored > 0


def test_the_limit_is_put_back_after_a_restore_in_the_callers_transaction(db_session, small_ceiling) -> None:
    """The script's path: a SAVEPOINT inside the caller's transaction.

    A released savepoint keeps its ``SET LOCAL``, so without the explicit
    reset the caller's later statements would run with the ceiling.
    """
    archive = _archive_then_limit(db_session, 15_000)
    assert db_session.in_transaction()
    restore_archive(db_session, io.BytesIO(archive))
    assert _show_ms(db_session) == 15_000


def test_the_limit_is_put_back_when_the_restore_fails(db_session, small_ceiling) -> None:
    archive = _archive_then_limit(db_session, 15_000)
    # A corrupt archive fails validation before the write transaction; make the
    # failure happen inside it instead by breaking the write itself.
    db_session.execute(
        text(
            "CREATE FUNCTION pg_temp.refuse_restore_insert() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'restore refused by test'; END $$"
        )
    )
    db_session.execute(
        text(
            "CREATE TRIGGER refuse_restore_insert BEFORE INSERT ON reaction_family "
            "FOR EACH STATEMENT EXECUTE FUNCTION pg_temp.refuse_restore_insert()"
        )
    )
    with pytest.raises(Exception, match="restore refused by test"):
        restore_archive(db_session, io.BytesIO(archive))
    assert _show_ms(db_session) == 15_000


@pytest.mark.parametrize("previous_ms", [0, TEST_CEILING_MS * 2])
def test_the_helper_never_lowers_or_adds_a_limit(db_session, small_ceiling, previous_ms) -> None:
    """``0`` (operator disabled it) stays ``0``; a longer limit stays longer."""
    db_session.execute(text(f"SET LOCAL statement_timeout = {previous_ms}"))
    with archive_core._long_restore_statements(db_session):
        assert _show_ms(db_session) == previous_ms
    assert _show_ms(db_session) == previous_ms


def test_the_helper_raises_a_shorter_limit_and_restores_it(db_session, small_ceiling) -> None:
    db_session.execute(text("SET LOCAL statement_timeout = 7000"))
    with archive_core._long_restore_statements(db_session):
        assert _show_ms(db_session) == TEST_CEILING_MS
    assert _show_ms(db_session) == 7000
