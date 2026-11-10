"""The revision that says whether a parser-read parameter row was requested or reported.

Disposable database: existing parser rows are stamped ``requested``, upload and curated rows stay NULL; the constraint
refuses a kind on a non-parser row and ``reported`` on a deck row; the new vocabulary is added; downgrade deletes the
``reported`` rows, removes the vocabulary, the constraint, the column and the enum type; up again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("c8e2a6d1f305")
_KEYS = ("grid.integration_accuracy", "grid.angular_max", "scf.tol_energy_eh", "scf.tol_rms_density")


def _alembic(root: Path, env: dict, *args: str) -> None:
    subprocess.run(["conda", "run", "-n", "tckdb_env", "alembic", *args], cwd=root, env=env, check=True)


def test_statement_kind_revision_up_down_up():
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("param_statement_kind_migration")
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
                    "VALUES ('molecule', 'C', 'MIGRATION000000000000000004', 0, 1, 'achiral') RETURNING id"
                )
            )
            entry_id = conn.scalar(text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id})
            calc_id = conn.scalar(
                text("INSERT INTO calculation (type, quality, species_entry_id) VALUES ('sp', 'raw', :e) RETURNING id"),
                {"e": entry_id},
            )
            conn.execute(
                text(
                    "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, source, artifact_kind) "
                    "VALUES (:c, 'deck', '1', 'parser', 'input'), (:c, 'echo', '2', 'parser', 'output_log')"
                ),
                {"c": calc_id},
            )
            conn.execute(
                text(
                    "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, source) "
                    "VALUES (:c, 'typed', '3', 'upload'), (:c, 'curated', '4', 'curated')"
                ),
                {"c": calc_id},
            )

        _alembic(root, env, "upgrade", _MIGRATION.revision)
        with engine.begin() as conn:
            kinds = dict(conn.execute(text("SELECT raw_key, statement_kind::text FROM calculation_parameter")).all())
            assert kinds == {"deck": "requested", "echo": "requested", "typed": None, "curated": None}
            assert set(conn.scalars(text("SELECT canonical_key FROM calculation_parameter_vocab WHERE canonical_key = ANY(:k)"), {"k": list(_KEYS)})) == set(_KEYS)
            conn.execute(
                text(
                    "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, source, artifact_kind, statement_kind) "
                    "VALUES (:c, 'rep', '5', 'parser', 'output_log', 'reported')"
                ),
                {"c": calc_id},
            )
        for source, artifact, kind in (("upload", None, "requested"), ("parser", "input", "reported"), ("curated", None, "reported")):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, source, artifact_kind, statement_kind) "
                            "VALUES (:c, 'bad', '6', :s, CAST(:a AS artifact_kind), CAST(:k AS parameter_statement_kind))"
                        ),
                        {"c": calc_id, "s": source, "a": artifact, "k": kind},
                    )

        _alembic(root, env, "downgrade", _MIGRATION.parent)
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    text("SELECT 1 FROM information_schema.columns WHERE table_name='calculation_parameter' AND column_name='statement_kind'")
                )
                is None
            )
            assert conn.scalar(text("SELECT 1 FROM pg_type WHERE typname = 'parameter_statement_kind'")) is None
            assert set(conn.scalars(text("SELECT raw_key FROM calculation_parameter"))) == {"deck", "echo", "typed", "curated"}
            assert not list(conn.scalars(text("SELECT canonical_key FROM calculation_parameter_vocab WHERE canonical_key = ANY(:k)"), {"k": list(_KEYS)}))

        _alembic(root, env, "upgrade", _MIGRATION.revision)
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT statement_kind::text FROM calculation_parameter WHERE raw_key = 'echo'")) == "requested"
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"), {"name": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()
