"""The machine-review lesson revision round-trips and its enum labels match the Python enums."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.db.models.common import (
    MachineReviewCategory,
    MachineReviewLessonSignal,
    MachineReviewLessonStatus,
)
from tests.db._migration_chain import current_head, script_directory

REPO_BACKEND = Path(__file__).resolve().parents[2]
REVISION = "e7a3c9d1b5f2"
PREVIOUS = "b4d7f1a6c3e9"
TABLES = {"machine_review_lesson"}
ENUMS = {
    "machine_review_category": MachineReviewCategory,
    "machine_review_lesson_status": MachineReviewLessonStatus,
    "machine_review_lesson_signal": MachineReviewLessonSignal,
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

    db_name = scratch_database_name("machine_review_lessons")
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
        tables = {
            name
            for (name,) in conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_name = 'machine_review_lesson'")
            )
        }
        labels = {
            typ: [
                label
                for (label,) in conn.execute(
                    text(
                        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = :t ORDER BY e.enumsortorder"
                    ),
                    {"t": typ},
                )
            ]
            for typ in ENUMS
        }
        indexes = {
            name
            for (name,) in conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'machine_review_lesson'"))
        }
        columns = {
            name
            for (name,) in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'machine_review_lesson'")
            )
        }
    return {"tables": tables, "labels": labels, "indexes": indexes, "columns": columns}


def test_the_revision_sits_on_the_previous_head_and_is_the_only_head():
    assert script_directory().get_revision(REVISION).down_revision == PREVIOUS
    assert current_head() == REVISION
    assert script_directory().get_heads() == [REVISION]


def test_upgrade_downgrade_upgrade_round_trips(scratch_db):
    engine, env = scratch_db
    _alembic("upgrade", REVISION, env)
    built = _state(engine)
    assert built["tables"] == TABLES
    assert built["labels"] == {name: [m.value for m in enum] for name, enum in ENUMS.items()}
    assert {"ix_machine_review_lesson_status_verified_at", "ix_machine_review_lesson_status_created_at"} <= built[
        "indexes"
    ]
    assert not {"search_vector"} & built["columns"]
    assert not [i for i in built["indexes"] if "gin" in i]

    _alembic("downgrade", PREVIOUS, env)
    gone = _state(engine)
    assert gone["tables"] == set()
    assert gone["labels"] == {name: [] for name in ENUMS}
    assert not [i for i in gone["indexes"] if i.startswith("ix_machine_review_lesson")]

    _alembic("upgrade", REVISION, env)
    assert _state(engine) == built
