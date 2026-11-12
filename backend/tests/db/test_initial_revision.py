"""The single revision builds the schema and its seed rows, and downgrade returns the database to empty."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.db._migration_chain import base_revision, current_head

REPO_BACKEND = Path(__file__).resolve().parents[2]

_OWNED_OBJECTS = {
    "tables": (
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S') AND c.relname <> 'alembic_version' "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')"
    ),
    "functions": (
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')"
    ),
    "enum types": (
        "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = 'public' AND t.typtype = 'e'"
    ),
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

    db_name = scratch_database_name("initial_revision")
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


def _counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {name: conn.scalar(text(sql)) for name, sql in _OWNED_OBJECTS.items()}


def _seed_counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table: conn.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("reaction_family", "calculation_parameter_vocab", "conformer_assignment_scheme")
        }


def test_the_chain_is_one_revision():
    assert base_revision() == current_head()


def test_upgrade_builds_the_schema_and_downgrade_removes_all_of_it(scratch_db):
    engine, env = scratch_db

    _alembic("upgrade", "head", env)
    built = _counts(engine)
    seeded = _seed_counts(engine)
    assert all(count > 0 for count in built.values()), built
    assert all(count > 0 for count in seeded.values()), seeded
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == current_head()
        assert conn.scalar(text("SELECT count(*) FROM pg_extension WHERE extname = 'rdkit'")) == 1

    _alembic("downgrade", "base", env)
    assert _counts(engine) == dict.fromkeys(_OWNED_OBJECTS, 0)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM alembic_version")) == 0

    _alembic("upgrade", "head", env)
    assert _counts(engine) == built
    assert _seed_counts(engine) == seeded
