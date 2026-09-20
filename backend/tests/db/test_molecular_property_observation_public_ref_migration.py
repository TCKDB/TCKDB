"""Database contracts for the ``molecular_property_observation`` public-ref revision.

``d2f4a7c1b8e6`` gives every molecular-property observation a citable
``mpo_`` handle, backfilled for rows that predate the column. Same
server-side base32 function + ``server_default`` backfill shape as
``c41dfc710b81`` (``calculation_artifact``) and ``c8b4e1a7d302``
(``artifact_integrity_event``): no append-only trigger, since this table
predates the mixin by convention rather than by trigger.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("d2f4a7c1b8e6")


def test_migration_source_matches_backfill_contract():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "d2f4a7c1b8e6_molecular_property_observation_public_ref.py"
    )
    source = path.read_text()
    assert (
        f'down_revision: Union[str, Sequence[str], None] = "{_MIGRATION.parent}"'
        in source
    )
    assert "SET public_ref = DEFAULT" in source
    assert "nullable=False" in source
    assert "unique=True" in source
    assert "CREATE TRIGGER" not in source
    assert "DROP TRIGGER" not in source


def test_public_ref_database_contract(db_session):
    column = db_session.execute(
        text(
            """
            SELECT is_nullable, column_default FROM information_schema.columns
            WHERE table_name = 'molecular_property_observation' AND column_name = 'public_ref'
            """
        )
    ).one()
    assert column.is_nullable == "NO"
    assert "mpo_" in column.column_default

    index_kind = db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'molecular_property_observation'
              AND indexname = 'ix_molecular_property_observation_public_ref'
            """
        )
    ).scalar()
    assert index_kind is not None
    assert "UNIQUE" in index_kind


def test_raw_sql_fallback_has_standard_base32_shape(db_session):
    ref = db_session.scalar(text("SELECT public.mpo_opaque_public_ref()"))
    assert re.fullmatch(r"mpo_[a-z2-7]{26}", ref)
    ref2 = db_session.scalar(text("SELECT public.mpo_opaque_public_ref()"))
    assert ref != ref2


def test_legacy_upgrade_backfills_existing_rows():
    """Seed rows at the parent revision (pre-migration shape), upgrade, and
    confirm every row picks up a distinct, correctly-shaped ref -- including
    a raw-SQL insert made *after* the upgrade, which must fall back to the
    server-side function rather than violate NOT NULL.
    """
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("mpo_pubref")
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        env = _db_env(db_name)
        root = Path(__file__).resolve().parents[2]
        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", _MIGRATION.parent],
            cwd=root, env=env, check=True,
        )
        engine = create_engine(_database_url(db_name))

        with engine.begin() as conn:
            obs_ids = conn.scalars(
                text(
                    "INSERT INTO molecular_property_observation "
                    "(scientific_origin, property_kind, scalar_value, scalar_unit) "
                    "VALUES ('experimental', 'dipole_moment', 1.85, 'D'), "
                    "       ('experimental', 'ionization_energy', 10.5, 'eV') "
                    "RETURNING id"
                )
            ).all()

        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", _MIGRATION.revision],
            cwd=root, env=env, check=True,
        )
        with engine.begin() as conn:
            refs = conn.scalars(
                text(
                    "SELECT public_ref FROM molecular_property_observation "
                    "WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": obs_ids},
            ).all()
            assert len(refs) == 2
            assert len(set(refs)) == 2, "backfill must not collide same-content rows"
            assert all(re.fullmatch(r"mpo_[a-z2-7]{26}", ref) for ref in refs)

            raw_ref = conn.scalar(
                text(
                    "INSERT INTO molecular_property_observation "
                    "(scientific_origin, property_kind, scalar_value, scalar_unit) "
                    "VALUES ('computed', 'polarizability_iso', 12.3, 'bohr^3') "
                    "RETURNING public_ref"
                )
            )
            assert re.fullmatch(r"mpo_[a-z2-7]{26}", raw_ref)

            with pytest.raises(DBAPIError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO molecular_property_observation "
                            "(scientific_origin, property_kind, scalar_value, "
                            "scalar_unit, public_ref) "
                            "VALUES ('computed', 'homo_energy', -9.1, 'eV', NULL)"
                        )
                    )
    finally:
        if engine:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:n"),
                {"n": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()
