"""Database contracts for the reproducibility-assessment public-ref revision."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "6a9d2e4c7b1f_add_repro_assessment_public_ref.py"


def test_migration_backfills_with_trigger_restoration_contract():
    source = MIGRATION.read_text()
    assert 'down_revision: Union[str, Sequence[str], None] = "f2a4c6e8b0d1"' in source
    assert "DROP TRIGGER IF EXISTS" in source
    assert "SET public_ref = DEFAULT" in source
    assert "CREATE TRIGGER" in source
    assert "nullable=False" in source
    assert "unique=True" in source


def test_public_ref_database_contract_and_append_only_trigger(db_session):
    column = db_session.execute(text("""
        SELECT is_nullable, column_default FROM information_schema.columns
        WHERE table_name = 'record_reproducibility_assessment' AND column_name = 'public_ref'
    """)).one()
    assert column.is_nullable == "NO"
    assert "rpa_" in column.column_default
    trigger = db_session.scalar(text("""
        SELECT tgname FROM pg_trigger WHERE tgrelid = 'record_reproducibility_assessment'::regclass
          AND tgname = 'trg_repro_assessment_append_only' AND NOT tgisinternal
    """))
    assert trigger == "trg_repro_assessment_append_only"


def test_raw_sql_fallback_has_standard_base32_shape(db_session):
    ref = db_session.scalar(text("SELECT public.rpa_opaque_public_ref()"))
    assert re.fullmatch(r"rpa_[a-z2-7]{26}", ref)


def test_legacy_upgrade_backfills_refs_and_restores_append_only():
    """Exercise the real revision against a UUID-named disposable database."""
    from conftest import _database_url, _db_env

    db_name = f"tckdb_rpa_migration_{uuid4().hex}"
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        env = _db_env(db_name)
        root = Path(__file__).resolve().parents[2]
        for revision in ("f2a4c6e8b0d1",):
            subprocess.run(["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", revision], cwd=root, env=env, check=True)
        engine = create_engine(_database_url(db_name))
        statement = text("""INSERT INTO record_reproducibility_assessment
          (record_type,record_id,grade,rubric_name,rubric_version,context_hash,context_json,passed_json,missing_json,warnings_json,assessor_kind)
          VALUES ('thermo',:id,'described','legacy','v1',:hash,'{}'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'system')""")
        with engine.begin() as conn:
            conn.execute(statement, [{"id": 101, "hash": "a" * 64}, {"id": 102, "hash": "b" * 64}])
        subprocess.run(["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "6a9d2e4c7b1f"], cwd=root, env=env, check=True)
        with engine.begin() as conn:
            refs = conn.scalars(text("SELECT public_ref FROM record_reproducibility_assessment ORDER BY record_id")).all()
            assert len(set(refs)) == 2 and all(re.fullmatch(r"rpa_[a-z2-7]{26}", ref) for ref in refs)
            raw = conn.scalar(text("""INSERT INTO record_reproducibility_assessment
              (record_type,record_id,grade,rubric_name,rubric_version,context_hash,context_json,passed_json,missing_json,warnings_json,assessor_kind)
              VALUES ('thermo',103,'described','raw','v1',:hash,'{}'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'system') RETURNING public_ref"""), {"hash": "c" * 64})
            assert re.fullmatch(r"rpa_[a-z2-7]{26}", raw)
            with pytest.raises(DBAPIError, match="append-only"):
                with conn.begin_nested():
                    conn.execute(text("UPDATE record_reproducibility_assessment SET rubric_name='bad' WHERE record_id=101"))
            with pytest.raises(DBAPIError, match="append-only"):
                with conn.begin_nested():
                    conn.execute(text("DELETE FROM record_reproducibility_assessment WHERE record_id=102"))
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:n"), {"n": db_name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()
