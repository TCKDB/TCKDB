"""The harness makes a stuck or slow statement explain itself.

Without it a one-off statement timeout in CI leaves nothing to read (see
``backend/docs/testing.md``, "Slow-statement diagnostics").
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tests.conftest import TIMEOUT_PLAN_MARKER


def _is_superuser(db_conn) -> bool:
    return db_conn.execute(text("SELECT current_setting('is_superuser')")).scalar() == "on"


def test_the_test_database_logs_lock_waits_and_slow_plans(db_conn):
    if not _is_superuser(db_conn):
        pytest.skip("role cannot ALTER DATABASE SET; the harness skipped the diagnostics too")
    assert db_conn.execute(text("SHOW log_lock_waits")).scalar() == "on"
    assert db_conn.execute(text("SHOW deadlock_timeout")).scalar() == "1s"
    # Whenever the library loads here, the harness must have wired it in: a
    # conditional on the preload setting itself would pass with it removed.
    db_conn.execute(text("SAVEPOINT probe"))
    try:
        db_conn.execute(text("LOAD 'auto_explain'"))
    except Exception:
        db_conn.execute(text("ROLLBACK TO SAVEPOINT probe"))
        pytest.skip("auto_explain does not load on this server; the harness skipped it too")
    assert "auto_explain" in db_conn.execute(text("SHOW session_preload_libraries")).scalar()
    assert db_conn.execute(text("SHOW auto_explain.log_min_duration")).scalar() == "3s"
    assert db_conn.execute(text("SHOW auto_explain.log_analyze")).scalar() == "off"
    assert db_conn.execute(text("SHOW auto_explain.log_nested_statements")).scalar() == "on"


def test_a_statement_timeout_prints_the_plan_of_the_cancelled_statement(db_engine, capsys):
    with db_engine.connect() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '200ms'"))
        with pytest.raises(OperationalError) as caught:
            # One aggregate row, so pg_sleep runs once even on an empty table.
            connection.execute(text("SELECT pg_sleep(1), count(*) FROM species WHERE id > :floor"), {"floor": 0})
    assert getattr(caught.value.orig, "sqlstate", None) == "57014"  # the original error is untouched
    captured = capsys.readouterr().err
    assert TIMEOUT_PLAN_MARKER in captured
    assert "species" in captured and "Scan" in captured, captured


def test_a_timeout_on_a_statement_explain_cannot_handle_prints_why_and_keeps_the_error(db_engine, capsys):
    with db_engine.connect() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '200ms'"))
        with pytest.raises(OperationalError) as caught:
            connection.execute(text("DO $$ BEGIN PERFORM pg_sleep(1); END $$"))
    assert getattr(caught.value.orig, "sqlstate", None) == "57014"  # the hook did not replace the error
    assert f"{TIMEOUT_PLAN_MARKER} unavailable" in capsys.readouterr().err


def _context(engine, statement, parameters):
    from types import SimpleNamespace

    class Cancelled(Exception):
        sqlstate = "57014"

    return SimpleNamespace(original_exception=Cancelled(), statement=statement, parameters=parameters, engine=engine)


def test_a_literal_percent_with_empty_parameters_is_explained(db_engine, capsys):
    from tests.conftest import _explain_statement_on_timeout

    _explain_statement_on_timeout(_context(db_engine, "SELECT id %% 2 FROM species", {}))
    err = capsys.readouterr().err
    assert "unavailable" not in err and "Scan" in err, err


@pytest.mark.parametrize(
    ("statement", "parameters", "reason"),
    [
        ("INSERT INTO species (id) VALUES (%(id)s)", [{"id": 1}, {"id": 2}], "executemany"),
        ("EXPLAIN SELECT 1", {}, "already an EXPLAIN"),
    ],
)
def test_a_statement_that_cannot_be_explained_prints_the_reason(db_engine, capsys, statement, parameters, reason):
    from tests.conftest import _explain_statement_on_timeout

    _explain_statement_on_timeout(_context(db_engine, statement, parameters))
    assert f"{TIMEOUT_PLAN_MARKER} unavailable ({reason})" in capsys.readouterr().err
