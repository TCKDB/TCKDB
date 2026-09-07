"""Database contracts for the ``calculation_artifact`` public-ref revision.

``c41dfc710b81`` gives every uploaded artifact a citable ``art_`` handle,
backfilled for rows that predate the column. This mirrors
``c8b4e1a7d302_artifact_integrity_event_public_ref.py`` and
``6a9d2e4c7b1f_add_repro_assessment_public_ref.py``: same server-side base32
function + ``server_default`` backfill shape, no append-only trigger (this
table is append-only by convention, same as ``artifact_integrity_event``).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("c41dfc710b81")


def test_migration_source_matches_backfill_contract():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "c41dfc710b81_calculation_artifact_public_ref.py"
    )
    source = path.read_text()
    assert f'down_revision: Union[str, Sequence[str], None] = "{_MIGRATION.parent}"' in source
    assert "SET public_ref = DEFAULT" in source
    assert "nullable=False" in source
    assert "unique=True" in source
    # No append-only trigger here (unlike 6a9d2e4c7b1f) -- the table has none.
    assert "CREATE TRIGGER" not in source
    assert "DROP TRIGGER" not in source


def test_public_ref_database_contract(db_session):
    column = db_session.execute(
        text(
            """
            SELECT is_nullable, column_default FROM information_schema.columns
            WHERE table_name = 'calculation_artifact' AND column_name = 'public_ref'
            """
        )
    ).one()
    assert column.is_nullable == "NO"
    assert "art_" in column.column_default

    index_kind = db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'calculation_artifact'
              AND indexname = 'ix_calculation_artifact_public_ref'
            """
        )
    ).scalar()
    assert index_kind is not None
    assert "UNIQUE" in index_kind


def test_raw_sql_fallback_has_standard_base32_shape(db_session):
    ref = db_session.scalar(text("SELECT public.art_opaque_public_ref()"))
    assert re.fullmatch(r"art_[a-z2-7]{26}", ref)
    # Two calls must not collide.
    ref2 = db_session.scalar(text("SELECT public.art_opaque_public_ref()"))
    assert ref != ref2


def test_legacy_upgrade_backfills_existing_rows():
    """Exercise the real revision against a disposable database: seed rows
    at the parent revision (as a pre-migration deployment would have them),
    upgrade, and confirm every row picks up a distinct, correctly-shaped
    ref -- including a raw-SQL insert made *after* the upgrade, which must
    fall back to the server-side function rather than violate NOT NULL.
    """
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("calc_artifact_pubref")
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

        # Seed a minimal species/species_entry/calculation chain, then two
        # calculation_artifact rows -- pre-migration shape, no public_ref.
        with engine.begin() as conn:
            species_id = conn.scalar(
                text(
                    "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                    "VALUES ('molecule', 'O', :inchi_key, 0, 1, 'achiral') RETURNING id"
                ),
                {"inchi_key": "XLYOFNOQVPJJNP-PUBREFXXX-N"},
            )
            entry_id = conn.scalar(
                text("INSERT INTO species_entry (species_id) VALUES (:sid) RETURNING id"),
                {"sid": species_id},
            )
            calc_id = conn.scalar(
                text(
                    "INSERT INTO calculation (type, species_entry_id) "
                    "VALUES ('sp', :eid) RETURNING id"
                ),
                {"eid": entry_id},
            )
            artifact_ids = conn.scalars(
                text(
                    "INSERT INTO calculation_artifact "
                    "(calculation_id, kind, uri, sha256, bytes, filename) "
                    "VALUES (:cid, 'output_log', 's3://bucket/a.log', :sha, 10, 'a.log'), "
                    "       (:cid, 'output_log', 's3://bucket/b.log', :sha, 10, 'b.log') "
                    "RETURNING id"
                ),
                {"cid": calc_id, "sha": "9" * 64},
            ).all()

        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", _MIGRATION.revision],
            cwd=root, env=env, check=True,
        )
        with engine.begin() as conn:
            refs = conn.scalars(
                text("SELECT public_ref FROM calculation_artifact WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": artifact_ids},
            ).all()
            assert len(refs) == 2
            assert len(set(refs)) == 2, "backfill must not collide same-sha256 rows"
            assert all(re.fullmatch(r"art_[a-z2-7]{26}", ref) for ref in refs)

            # A raw insert after the upgrade also gets a ref via server_default.
            raw_ref = conn.scalar(
                text(
                    "INSERT INTO calculation_artifact "
                    "(calculation_id, kind, uri, sha256, bytes, filename) "
                    "VALUES (:cid, 'output_log', 's3://bucket/c.log', :sha, 10, 'c.log') "
                    "RETURNING public_ref"
                ),
                {"cid": calc_id, "sha": "8" * 64},
            )
            assert re.fullmatch(r"art_[a-z2-7]{26}", raw_ref)

            # NOT NULL is enforced: an explicit NULL is rejected.
            with pytest.raises(DBAPIError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO calculation_artifact "
                            "(calculation_id, kind, uri, sha256, bytes, filename, public_ref) "
                            "VALUES (:cid, 'output_log', 's3://bucket/d.log', :sha, 10, 'd.log', NULL)"
                        ),
                        {"cid": calc_id, "sha": "6" * 64},
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
