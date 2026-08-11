"""A repair declaration dies with its transaction, and its record does not.

This is the property that makes ``e2c9a4f7b163`` a repair path rather than a
bypass: there is no close step to forget and no flag to reset, because the
declaration is matched on ``pg_current_xact_id()`` and a committed transaction
can never present that id again. Its symmetric half is that the *record* has
to survive the same commit, since an audit trail scoped to a transaction would
be no trail at all.

Neither half can be shown on the shared test database, which rolls every test
back and whose committed-row tripwire exists precisely to stop a test writing
through that rollback. So this file builds a throwaway database, migrates it,
and commits on it -- the only arrangement in which "what is true after COMMIT"
is a question that can be asked at all.

It is one database and one migration run (about four seconds) shared by every
test here, rather than one per test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def committed_engine() -> Iterator:
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("repair_lifetime")
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{db_name}"'))

    engine = None
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=os.fspath(_BACKEND_ROOT),
            env=_db_env(db_name),
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        # Reproduce a database whose stored rows predate ``c5a1f8e3d074``.
        # That revision added ``ck_geometry_atom_element_canonical``, so ``CL``
        # can no longer be written -- and the rows a repair exists for are
        # exactly the rows a later rule would refuse, because a repair is
        # needed when the data predates the constraint and never when it was
        # written under it. This module commits for real, which is the point of
        # it, so the drop cannot be transaction-local; it is confined to this
        # module's own throwaway database, which is dropped below.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE geometry_atom DROP CONSTRAINT "
                    "ck_geometry_atom_element_canonical"
                )
            )
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"),
                {"name": db_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()


def _seed_accepted_atom(connection) -> dict[str, int]:
    """One accepted calculation over one geometry with one shouted atom."""
    suffix = uuid4().hex[:8]
    # Species identity is (smiles, charge, multiplicity), and this database
    # outlives each test in the module, so the SMILES has to be distinct per
    # call rather than a literal.
    smiles = "C" * (1 + connection.scalar(text("SELECT count(*) FROM species")))
    species_id = connection.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral') RETURNING id"
        ),
        {"smiles": smiles, "inchi_key": f"REPAIRLIFE{suffix}AAAAAAAAAAA"[:27]},
    )
    species_entry_id = connection.scalar(
        text("INSERT INTO species_entry (species_id, unmapped_smiles) VALUES (:species_id, :smiles) RETURNING id"),
        {"species_id": species_id, "smiles": smiles},
    )
    geometry_id = connection.scalar(
        text("INSERT INTO geometry (natoms, geom_hash, xyz_text) VALUES (1, :geom_hash, '1\n\nCL 0 0 0') RETURNING id"),
        {"geom_hash": uuid4().hex + uuid4().hex},
    )
    connection.execute(
        text(
            "INSERT INTO geometry_atom (geometry_id, atom_index, element, x, y, z) "
            "VALUES (:geometry_id, 1, 'CL', 0, 0, 0)"
        ),
        {"geometry_id": geometry_id},
    )
    calculation_id = connection.scalar(
        text("INSERT INTO calculation (type, species_entry_id) VALUES ('opt', :species_entry_id) RETURNING id"),
        {"species_entry_id": species_entry_id},
    )
    connection.execute(
        text(
            "INSERT INTO calculation_input_geometry (calculation_id, geometry_id, input_order) "
            "VALUES (:calculation_id, :geometry_id, 1)"
        ),
        {"calculation_id": calculation_id, "geometry_id": geometry_id},
    )
    reviewer_id = connection.scalar(
        text("INSERT INTO app_user (username, role) VALUES (:username, 'curator') RETURNING id"),
        {"username": f"repair-lifetime-{suffix}"},
    )
    connection.execute(
        text(
            "INSERT INTO record_review "
            "(record_type, record_id, status, reviewed_by, reviewed_at, first_approved_at) "
            "VALUES ('calculation', :calculation_id, 'approved', :reviewer_id, now(), now())"
        ),
        {"calculation_id": calculation_id, "reviewer_id": reviewer_id},
    )
    return {"geometry_id": geometry_id, "calculation_id": calculation_id}


_DECLARE = text(
    "INSERT INTO accepted_science_repair "
    "(target_table, declared_columns, alembic_revision, reason) "
    "VALUES ('geometry_atom', ARRAY['element'], 'e2c9a4f7b163', "
    " 'canonicalise element symbol case; no coordinate change') RETURNING id"
)

_REPAIR = text("UPDATE geometry_atom SET element = 'Cl' WHERE geometry_id = :geometry_id")


def test_a_declaration_does_not_survive_its_own_commit(committed_engine) -> None:
    with committed_engine.begin() as connection:
        seeded = _seed_accepted_atom(connection)
        repair_id = connection.scalar(_DECLARE)
        connection.execute(_REPAIR, {"geometry_id": seeded["geometry_id"]})

    # A second transaction, with the declaration committed and visible.
    with committed_engine.connect() as connection:
        with connection.begin():
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM accepted_science_repair WHERE id = :id"),
                    {"id": repair_id},
                )
                == 1
            )

        with pytest.raises(DBAPIError, match="is immutable"):
            with connection.begin():
                connection.execute(
                    text("UPDATE geometry_atom SET element = 'CL' WHERE geometry_id = :geometry_id"),
                    {"geometry_id": seeded["geometry_id"]},
                )

        # And it cannot be re-armed by pointing a fresh change row at it,
        # which is what would make the committed row a standing permission.
        with pytest.raises(DBAPIError, match="only be recorded in the transaction that declared it"):
            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO accepted_science_repair_change "
                        "(repair_id, record_type, record_id, target_schema, target_table, "
                        " row_identity, changed_columns, before_json, after_json) "
                        "VALUES (:repair_id, 'calculation', :calculation_id, 'public', "
                        " 'geometry_atom', '{\"geometry_id\": 1}'::jsonb, ARRAY['element'], "
                        " '{}'::jsonb, '{}'::jsonb)"
                    ),
                    {"repair_id": repair_id, "calculation_id": seeded["calculation_id"]},
                )


def test_the_record_of_a_repair_survives_the_commit(committed_engine) -> None:
    """What an auditor can reconstruct, read back after the fact."""
    with committed_engine.begin() as connection:
        seeded = _seed_accepted_atom(connection)
        repair_id = connection.scalar(_DECLARE)
        connection.execute(_REPAIR, {"geometry_id": seeded["geometry_id"]})

    with committed_engine.connect() as connection:
        declaration = (
            connection.execute(
                text(
                    "SELECT target_schema, target_table, declared_columns, alembic_revision, "
                    "reason, declared_by_role FROM accepted_science_repair WHERE id = :id"
                ),
                {"id": repair_id},
            )
            .mappings()
            .one()
        )
        assert declaration["target_table"] == "geometry_atom"
        assert declaration["declared_columns"] == ["element"]
        assert declaration["alembic_revision"] == "e2c9a4f7b163"
        assert "canonicalise" in declaration["reason"]
        assert declaration["declared_by_role"]

        change = (
            connection.execute(
                text(
                    "SELECT record_type::text AS record_type, record_id, target_table, "
                    "row_identity, changed_columns, before_json, after_json "
                    "FROM accepted_science_repair_change WHERE repair_id = :id"
                ),
                {"id": repair_id},
            )
            .mappings()
            .one()
        )
        assert change["record_type"] == "calculation"
        assert change["record_id"] == seeded["calculation_id"]
        assert change["row_identity"] == {
            "geometry_id": seeded["geometry_id"],
            "atom_index": 1,
        }
        assert change["changed_columns"] == ["element"]
        assert change["before_json"]["element"].strip() == "CL"
        assert change["after_json"]["element"].strip() == "Cl"

        # The evidence the repair claims not to have touched, still untouched.
        geometry = (
            connection.execute(
                text("SELECT geom_hash, xyz_text FROM geometry WHERE id = :id"),
                {"id": seeded["geometry_id"]},
            )
            .mappings()
            .one()
        )
        assert "CL" in geometry["xyz_text"]
        assert geometry["geom_hash"]
