"""``deposit_warning_retirement`` is append-only like the table it points at."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parents[2]


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
