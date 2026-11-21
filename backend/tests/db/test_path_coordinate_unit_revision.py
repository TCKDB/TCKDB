"""The path-coordinate unit revision adds two nullable, default-free unit columns and downgrade removes them."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.db.models.common import PathCoordinateUnit
from tests.db._migration_chain import script_directory

REPO_BACKEND = Path(__file__).resolve().parents[2]
REVISION = "e3b7c1a9d452"
PREVIOUS = "a6fc04f5667e"
COLUMNS = {
    ("calc_path_search_result", "path_coordinate_unit"),
    ("calc_irc_result", "reaction_coordinate_unit"),
}


def _alembic(direction: str, revision: str, env: dict[str, str]) -> None:
    subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
        cwd=REPO_BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def scratch_db():
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("path_coordinate_unit")
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    engine = create_engine(_database_url(db_name), pool_pre_ping=True)
    try:
        yield engine, _db_env(db_name)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": db_name}
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()


def _state(engine) -> dict[str, object]:
    with engine.connect() as conn:
        columns = {
            (table, column): (udt, nullable, default)
            for table, column, udt, nullable, default in conn.execute(
                text(
                    "SELECT table_name, column_name, udt_name, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE column_name IN ('path_coordinate_unit', 'reaction_coordinate_unit')"
                )
            )
        }
        labels = [
            label
            for (label,) in conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'path_coordinate_unit' ORDER BY e.enumsortorder"
                )
            )
        ]
    return {"columns": columns, "labels": labels}


def test_the_revision_sits_on_the_previous_head():
    assert script_directory().get_revision(REVISION).down_revision == PREVIOUS


def test_upgrade_adds_the_unit_columns_and_downgrade_removes_them(scratch_db):
    engine, env = scratch_db

    _alembic("upgrade", REVISION, env)
    built = _state(engine)
    assert set(built["columns"]) == COLUMNS
    assert all(value == ("path_coordinate_unit", "YES", None) for value in built["columns"].values())
    assert built["labels"] == [member.value for member in PathCoordinateUnit]

    _alembic("downgrade", PREVIOUS, env)
    assert _state(engine) == {"columns": {}, "labels": []}

    _alembic("upgrade", REVISION, env)
    assert _state(engine) == built
