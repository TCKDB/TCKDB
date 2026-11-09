"""The revision that adds ``parsed_log_archive`` to ``hessian_source``.

Disposable database: the value is absent below the revision and present at it; a ``calc_hessian`` row can carry
it; downgrade refuses while such a row exists (older code cannot read that source) and is a no-op otherwise (an
enum value cannot be dropped, so it stays in the type); up again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("e3a9c5d7b216")


def _alembic(root: Path, env: dict, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", *args], cwd=root, env=env, check=check, capture_output=True, text=True
    )


def _values(conn) -> set[str]:
    return set(conn.scalars(text("SELECT unnest(enum_range(NULL::hessian_source))::text")))


def test_hessian_source_archive_revision_up_down_up():
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("hessian_source_archive_migration")
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        env, root = _db_env(db_name), Path(__file__).resolve().parents[2]
        _alembic(root, env, "upgrade", _MIGRATION.parent)
        engine = create_engine(_database_url(db_name))
        with engine.begin() as conn:
            assert "parsed_log_archive" not in _values(conn)

        _alembic(root, env, "upgrade", _MIGRATION.revision)
        with engine.begin() as conn:
            assert "parsed_log_archive" in _values(conn)
            species_id = conn.scalar(
                text(
                    "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                    "VALUES ('molecule', 'C', 'MIGRATION000000000000000004', 0, 1, 'achiral') RETURNING id"
                )
            )
            entry_id = conn.scalar(text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id})
            calc_id = conn.scalar(
                text("INSERT INTO calculation (type, species_entry_id) VALUES ('freq', :e) RETURNING id"), {"e": entry_id}
            )
            geometry_id = conn.scalar(
                text(
                    "INSERT INTO geometry (natoms, geom_hash, xyz_text, public_ref) "
                    "VALUES (2, 'MIGRATIONGEOMHASH0000000000000000000000000000000000000000000004', "
                    "'2\nH2\nH 0 0 0\nH 0 0 0.74', 'geom_migration_4') RETURNING id"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO calc_hessian (calculation_id, geometry_id, natoms, lower_triangle_hartree_bohr2, source) "
                    "VALUES (:c, :g, 2, ARRAY[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, "
                    "15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0], 'parsed_log_archive')"
                ),
                {"c": calc_id, "g": geometry_id},
            )

        refused = _alembic(root, env, "downgrade", _MIGRATION.parent, check=False)
        assert refused.returncode != 0
        assert "parsed_log_archive" in refused.stderr + refused.stdout

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM calc_hessian"))
        _alembic(root, env, "downgrade", _MIGRATION.parent)
        with engine.begin() as conn:
            assert "parsed_log_archive" in _values(conn)  # an enum value cannot be dropped

        _alembic(root, env, "upgrade", _MIGRATION.revision)
        with engine.begin() as conn:
            assert "parsed_log_archive" in _values(conn)
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"), {"name": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()
