"""The catalogue revision creates and fills the catalogue; downgrade removes it and keeps the literature rows."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.services.method_catalogue_seed import load_seed
from app.services.method_catalogue_sync import sync_catalogue
from tests.db._migration_chain import script_directory

REPO_BACKEND = Path(__file__).resolve().parents[2]
REVISION = "a6fc04f5667e"
TABLES = (
    "method_catalogue",
    "method_catalogue_key",
    "method_catalogue_literature",
    "basis_set_catalogue",
    "basis_set_catalogue_key",
    "basis_set_catalogue_literature",
)
ENUMS = ("catalogue_method_kind", "basis_set_role", "catalogue_reference_role")


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

    db_name = scratch_database_name("method_catalogue")
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
        present = {
            t
            for (t,) in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(:t)"),
                {"t": list(TABLES)},
            )
        }
        enums = {n for (n,) in conn.execute(text("SELECT typname FROM pg_type WHERE typname = ANY(:n)"), {"n": list(ENUMS)})}
        counts = {t: conn.scalar(text(f"SELECT count(*) FROM {t}")) for t in sorted(present)}
        literature = conn.scalar(text("SELECT count(*) FROM literature WHERE title_source = 'resolved_metadata'"))
    return {"tables": present, "enums": enums, "counts": counts, "literature": literature}


def test_the_revision_sits_on_the_previous_head():
    assert script_directory().get_revision(REVISION).down_revision == "a7c3e1f5b942"


def test_upgrade_fills_the_catalogue_and_downgrade_removes_it(scratch_db):
    engine, env = scratch_db
    seed = load_seed()

    _alembic("upgrade", REVISION, env)
    built = _state(engine)
    assert built["tables"] == set(TABLES) and built["enums"] == set(ENUMS)
    assert built["counts"]["method_catalogue"] == len(seed.methods)
    assert built["counts"]["basis_set_catalogue"] == len(seed.basis_sets)
    assert built["counts"]["method_catalogue_key"] == sum(len(m.keys) for m in seed.methods)
    assert built["literature"] == len(seed.references)
    with engine.connect() as conn:
        assert sync_catalogue(conn, seed, apply=False) == []

    _alembic("downgrade", "a7c3e1f5b942", env)
    removed = _state(engine)
    assert removed["tables"] == set() and removed["enums"] == set()
    assert removed["literature"] == len(seed.references)

    _alembic("upgrade", REVISION, env)
    assert _state(engine) == built
