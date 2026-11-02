"""The harness turns on lock-wait and slow-plan logging for each test database.

Without it a one-off statement timeout in CI leaves nothing to read (see
``backend/docs/testing.md``, "Slow-statement diagnostics").
"""

import pytest
from sqlalchemy import text


def test_the_test_database_logs_lock_waits_and_slow_plans(db_conn):
    if db_conn.execute(text("SELECT current_setting('is_superuser')")).scalar() != "on":
        pytest.skip("role cannot ALTER DATABASE SET; the harness skipped the diagnostics too")
    assert db_conn.execute(text("SHOW log_lock_waits")).scalar() == "on"
    assert db_conn.execute(text("SHOW deadlock_timeout")).scalar() == "1s"
    preload = db_conn.execute(text("SHOW session_preload_libraries")).scalar()
    if "auto_explain" in preload:
        assert db_conn.execute(text("SHOW auto_explain.log_min_duration")).scalar() == "3s"
        assert db_conn.execute(text("SHOW auto_explain.log_analyze")).scalar() == "off"
