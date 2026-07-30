"""Static migration contract for the execution-environment registry."""

import subprocess
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text


def test_execution_environment_migration_creates_and_reverses_complete_graph():
    migration = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/a8b9c0d1e2f3_add_execution_environment_manifests.py"
    ).read_text()
    for required in (
        'op.create_table(\n        "execution_environment_manifest"',
        'op.add_column("calculation", sa.Column("execution_environment_manifest_id"',
        'sa.Column("software_release_id", sa.BigInteger(), nullable=False)',
        'sa.Column("workflow_tool_release_id", sa.BigInteger(), nullable=True)',
        '"fk_execution_environment_manifest_software_release"',
        '"fk_execution_environment_manifest_workflow_tool_release"',
        '"fk_calculation_execution_environment_manifest"',
        '"ix_calculation_execution_environment_manifest_id"',
        "trg_execution_environment_manifest_immutable",
        "BEFORE UPDATE OR DELETE ON execution_environment_manifest",
        "trg_calculation_execution_environment_binding",
        "UPDATE OF execution_environment_manifest_id, software_release_id, workflow_tool_release_id ON calculation",
        'op.drop_column("calculation", "execution_environment_manifest_id")',
        'op.drop_table("execution_environment_manifest")',
    ):
        assert required in migration


def test_execution_environment_real_upgrade_and_downgrade_contract():
    """Exercise the revision on a disposable database, including nullable legacy rows."""
    from conftest import _database_url, _db_env

    db_name = f"tckdb_exec_env_migration_{uuid4().hex}"
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        env, root = _db_env(db_name), Path(__file__).resolve().parents[2]
        subprocess.run(["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "6a9d2e4c7b1f"], cwd=root, env=env, check=True)
        engine = create_engine(_database_url(db_name))
        with engine.begin() as conn:
            # Existing rows must survive without a manifest backfill.
            species_id = conn.scalar(
                text(
                    "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                    "VALUES ('molecule', 'C', 'MIGRATION000000000000000001', 0, 1, 'achiral') RETURNING id"
                )
            )
            species_entry_id = conn.scalar(
                text("INSERT INTO species_entry (species_id) VALUES (:species_id) RETURNING id"),
                {"species_id": species_id},
            )
            calc_id = conn.scalar(
                text(
                    "INSERT INTO calculation (type, quality, species_entry_id) "
                    "VALUES ('sp', 'raw', :species_entry_id) RETURNING id"
                ),
                {"species_entry_id": species_entry_id},
            )
        subprocess.run(["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "a8b9c0d1e2f3"], cwd=root, env=env, check=True)
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT execution_environment_manifest_id FROM calculation WHERE id = :id"), {"id": calc_id}) is None
            assert conn.scalar(text("SELECT tgname FROM pg_trigger WHERE tgrelid = 'execution_environment_manifest'::regclass AND tgname = 'trg_execution_environment_manifest_immutable'"))
            assert conn.scalar(text("SELECT tgname FROM pg_trigger WHERE tgrelid = 'calculation'::regclass AND tgname = 'trg_calculation_execution_environment_binding'"))
            assert conn.scalar(text("SELECT indexname FROM pg_indexes WHERE tablename='calculation' AND indexname='ix_calculation_execution_environment_manifest_id'"))
        subprocess.run(["conda", "run", "-n", "tckdb_env", "alembic", "downgrade", "6a9d2e4c7b1f"], cwd=root, env=env, check=True)
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT to_regclass('public.execution_environment_manifest')")) is None
            assert conn.scalar(text("SELECT 1 FROM information_schema.columns WHERE table_name='calculation' AND column_name='execution_environment_manifest_id'")) is None
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"), {"name": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()
