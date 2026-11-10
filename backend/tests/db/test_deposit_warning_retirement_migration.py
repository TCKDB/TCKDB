"""Migration coverage for ``deposit_warning_retirement`` (``b8e3f1a7c524``).

The revision is reversible, and the new table is append-only like the one it points at.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from tests.db._migration_chain import revision_under_test

REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = revision_under_test("b8e3f1a7c524")


def _configure(db_engine, monkeypatch) -> Config:
    db_name = db_engine.url.database
    db_engine.dispose()
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")
    return Config(str(REPO_ROOT / "alembic.ini"))


def _tables(connection) -> set[str]:
    return set(connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")))


def _functions(connection) -> set[str]:
    return set(connection.scalars(text("SELECT proname FROM pg_proc WHERE proname LIKE 'reject_deposit_warning%'")))


def test_downgrade_then_upgrade_round_trips_and_keeps_deposit_warning(db_engine, monkeypatch):
    config = _configure(db_engine, monkeypatch)
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            assert "deposit_warning_retirement" in _tables(connection)
        command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            tables = _tables(connection)
            assert "deposit_warning_retirement" not in tables
            assert "deposit_warning" in tables
            assert _functions(connection) == {"reject_deposit_warning_mutation"}
        command.upgrade(config, _MIGRATION.revision)
        with engine.connect() as connection:
            assert "deposit_warning_retirement" in _tables(connection)
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def test_a_retirement_row_cannot_be_updated_or_deleted(db_engine):
    """Runs in a rolled-back transaction."""
    insert_retirement = (
        "INSERT INTO deposit_warning_retirement (deposit_warning_id, retired_reason_code) VALUES (:w, :r) RETURNING id"
    )
    with db_engine.connect() as connection:
        outer = connection.begin()
        try:
            warning_id = connection.scalar(
                text(
                    "INSERT INTO deposit_warning (record_type, record_id, code, field, message, content_hash, check_version) "
                    "VALUES ('calculation', 1, 'c', 'f', 'm', repeat('a', 64), 1) RETURNING id"
                )
            )
            retirement_id = connection.scalar(text(insert_retirement), {"w": warning_id, "r": "artifact_attached"})
            for statement, params in (
                ("UPDATE deposit_warning_retirement SET retired_reason_code = 'superseded_by_recheck' WHERE id = :i", {"i": retirement_id}),
                ("DELETE FROM deposit_warning_retirement WHERE id = :i", {"i": retirement_id}),
            ):
                with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
                    connection.execute(text(statement), params)
            # one retirement per warning
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(text(insert_retirement), {"w": warning_id, "r": "artifact_attached"})
            # a closed set of reasons
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(text(insert_retirement), {"w": warning_id, "r": "whim"})
        finally:
            outer.rollback()


def test_truncate_is_refused(db_engine):
    with db_engine.connect() as connection:
        outer = connection.begin()
        try:
            with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
                connection.execute(text("TRUNCATE deposit_warning_retirement"))
        finally:
            outer.rollback()
