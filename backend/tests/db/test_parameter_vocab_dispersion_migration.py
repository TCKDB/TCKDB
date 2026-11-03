"""The vocabulary revision for dispersion, solvation, RI, frozen core and friends.

Runs on a disposable database: up, with a parameter row that uses one of the new keys, down (the row must
keep its raw key and value and lose only the canonical key), and up again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sqlalchemy import create_engine, text

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("e5b1c8a3d702")
_NEW_KEYS = {
    "dispersion.correction",
    "solvation.model",
    "solvation.solvent",
    "ri.approximation",
    "core.frozen",
    "relativistic.hamiltonian",
    "basis.d_function_type",
    "basis.f_function_type",
    "scf.stability_analysis",
}


def _alembic(root: Path, env: dict, *args: str) -> None:
    subprocess.run(["conda", "run", "-n", "tckdb_env", "alembic", *args], cwd=root, env=env, check=True)


def test_vocab_revision_up_down_up_keeps_raw_rows():
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("param_vocab_migration")
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        env, root = _db_env(db_name), Path(__file__).resolve().parents[2]
        _alembic(root, env, "upgrade", _MIGRATION.parent)
        engine = create_engine(_database_url(db_name))

        def vocab_keys() -> set[str]:
            with engine.begin() as conn:
                return set(
                    conn.scalars(
                        text("SELECT canonical_key FROM calculation_parameter_vocab WHERE canonical_key = ANY(:k)"),
                        {"k": sorted(_NEW_KEYS)},
                    )
                )

        assert vocab_keys() == set()
        _alembic(root, env, "upgrade", _MIGRATION.revision)
        assert vocab_keys() == _NEW_KEYS

        with engine.begin() as conn:
            species_id = conn.scalar(
                text(
                    "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                    "VALUES ('molecule', 'C', 'MIGRATION000000000000000002', 0, 1, 'achiral') RETURNING id"
                )
            )
            entry_id = conn.scalar(
                text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id}
            )
            calc_id = conn.scalar(
                text("INSERT INTO calculation (type, quality, species_entry_id) VALUES ('sp', 'raw', :e) RETURNING id"),
                {"e": entry_id},
            )
            conn.execute(
                text(
                    "INSERT INTO calculation_parameter (calculation_id, raw_key, raw_value, canonical_key, canonical_value) "
                    "VALUES (:c, 'D3BJ', 'true', 'dispersion.correction', 'd3_bj'), "
                    "(:c, 'TightSCF', 'true', 'scf.convergence', 'tight')"
                ),
                {"c": calc_id},
            )

        _alembic(root, env, "downgrade", _MIGRATION.parent)
        assert vocab_keys() == set()
        with engine.begin() as conn:
            rows = {
                r.raw_key: (r.canonical_key, r.canonical_value)
                for r in conn.execute(
                    text("SELECT raw_key, canonical_key, canonical_value FROM calculation_parameter WHERE calculation_id = :c"),
                    {"c": calc_id},
                )
            }
        assert rows == {"D3BJ": (None, None), "TightSCF": ("scf.convergence", "tight")}

        _alembic(root, env, "upgrade", _MIGRATION.revision)
        assert vocab_keys() == _NEW_KEYS
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"), {"name": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()
