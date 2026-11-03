"""The revision that tags a parser-read parameter row with the artifact kind it came from.

Disposable database: existing parser rows are stamped ``input`` and upload rows stay NULL; the constraint
refuses a kind on a non-parser row and a kind other than ``input``/``output_log``; downgrade removes the
``output_log`` rows (they could not be told from deck rows without the column) and the column; up again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("a3d7f1c5b904")


def _alembic(root: Path, env: dict, *args: str) -> None:
    subprocess.run(["conda", "run", "-n", "tckdb_env", "alembic", *args], cwd=root, env=env, check=True)


def test_artifact_kind_revision_up_down_up():
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("param_artifact_kind_migration")
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        env, root = _db_env(db_name), Path(__file__).resolve().parents[2]
        _alembic(root, env, "upgrade", _MIGRATION.parent)
        engine = create_engine(_database_url(db_name))
        with engine.begin() as conn:
            species_id = conn.scalar(
                text(
                    "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                    "VALUES ('molecule', 'C', 'MIGRATION000000000000000003', 0, 1, 'achiral') RETURNING id"
                )
            )
            entry_id = conn.scalar(text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id})
            calc_id = conn.scalar(
                text("INSERT INTO calculation (type, quality, species_entry_id) VALUES ('sp', 'raw', :e) RETURNING id"),
                {"e": entry_id},
            )
            conn.execute(
                text(
                    "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, source) "
                    "VALUES (:c, 'deck', '1', 'parser'), (:c, 'typed', '2', 'upload'), (:c, 'curated', '3', 'curated')"
                ),
                {"c": calc_id},
            )

        _alembic(root, env, "upgrade", _MIGRATION.revision)
        with engine.begin() as conn:
            kinds = dict(conn.execute(text("SELECT raw_key, artifact_kind::text FROM calculation_parameter")).all())
        assert kinds == {"deck": "input", "typed": None, "curated": None}

        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, source, artifact_kind) "
                    "VALUES (:c, 'echo', '4', 'parser', 'output_log')"
                ),
                {"c": calc_id},
            )
        for source, kind in (("upload", "input"), ("parser", "checkpoint")):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, source, artifact_kind) "
                            f"VALUES (:c, 'bad', '5', '{source}', '{kind}')"
                        ),
                        {"c": calc_id},
                    )

        _alembic(root, env, "downgrade", _MIGRATION.parent)
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    text("SELECT 1 FROM information_schema.columns WHERE table_name='calculation_parameter' AND column_name='artifact_kind'")
                )
                is None
            )
            assert set(conn.scalars(text("SELECT raw_key FROM calculation_parameter"))) == {"deck", "typed", "curated"}

        _alembic(root, env, "upgrade", _MIGRATION.revision)
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT artifact_kind::text FROM calculation_parameter WHERE raw_key = 'deck'")) == "input"
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"), {"name": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()
