"""Record deletion across committed transactions, on a throwaway database.

The rest of the suite runs inside one rolled-back transaction, so it cannot show that an audit row only opens the guards
in the transaction that wrote it, or that a release-selection writer waits on a deletion holding the record's lock.
These tests commit real rows, so they run on a database of their own that is dropped afterwards: a committed row in an
append-only table has no other cleanup path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.services.record_review import ensure_record_review, set_record_review_status
from app.services.release.curation import lock_record_for_selection
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
)

REPO_BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def committed_engine():
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("record_deletion_committed")
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    engine = create_engine(_database_url(db_name), pool_pre_ping=True)
    try:
        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
            cwd=REPO_BACKEND,
            env=_db_env(db_name),
            check=True,
            capture_output=True,
        )
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": db_name}
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()


def _committed_approved_kinetics(engine) -> tuple[int, str, int]:
    """An approved kinetics record and an admin, committed. Returns (kinetics id, public ref, admin id)."""
    with Session(engine) as session:
        admin = AppUser(username=f"committed-admin-{next_inchi_key('CA')[-6:]}", role=AppUserRole.admin)
        curator = AppUser(username=f"committed-curator-{next_inchi_key('CC')[-6:]}", role=AppUserRole.curator)
        session.add_all([admin, curator])
        session.flush()
        reactant = make_species(session, smiles="C=C", inchi_key=next_inchi_key("RDCR"))
        product = make_species(session, smiles="CC", inchi_key=next_inchi_key("RDCP"))
        reaction = make_chem_reaction(session, reactants=[reactant], products=[product])
        entry = make_reaction_entry(
            session,
            reaction=reaction,
            reactant_entries=[make_species_entry(session, species=reactant)],
            product_entries=[make_species_entry(session, species=product)],
        )
        kinetics = make_kinetics(session, reaction_entry=entry)
        ensure_record_review(session, record_type=SubmissionRecordType.kinetics, record_id=kinetics.id)
        set_record_review_status(
            session,
            record_type=SubmissionRecordType.kinetics,
            record_id=kinetics.id,
            status=RecordReviewStatus.approved,
            actor=curator,
        )
        result = (kinetics.id, kinetics.public_ref, admin.id)
        session.commit()
    return result


def test_an_audit_row_from_an_earlier_transaction_does_not_permit_a_later_delete(committed_engine) -> None:
    kinetics_id, ref, admin_id = _committed_approved_kinetics(committed_engine)
    with committed_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO record_deletion (record_type, record_id, record_public_ref, first_approved_at, reason, "
                "deleted_by, snapshot) VALUES ('kinetics', :id, :ref, now(), 'earlier', :by, '{}'::jsonb)"
            ),
            {"id": kinetics_id, "ref": ref, "by": admin_id},
        )
        conn.execute(text("SELECT set_config('tckdb.record_deletion', :v, true)"), {"v": f"kinetics:{kinetics_id}"})

    with committed_engine.connect() as conn, conn.begin():
        conn.execute(text("SELECT set_config('tckdb.record_deletion', :v, true)"), {"v": f"kinetics:{kinetics_id}"})
        with pytest.raises(DBAPIError) as raised:
            conn.execute(text("DELETE FROM kinetics WHERE id = :id"), {"id": kinetics_id})
        assert getattr(raised.value.orig, "sqlstate", None) == "55000"

    with committed_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM kinetics WHERE id = :id"), {"id": kinetics_id}).scalar() == 1


def test_a_selection_writer_waits_for_a_deletion_holding_the_record(committed_engine) -> None:
    """The deletion locks the record (``FOR UPDATE``) before its citation check; a selection must take that lock too,
    so it cannot slip in between the check and the delete."""
    kinetics_id, _ref, _admin_id = _committed_approved_kinetics(committed_engine)
    with committed_engine.connect() as deleting, deleting.begin():
        deleting.execute(text("SELECT id FROM kinetics WHERE id = :id FOR UPDATE"), {"id": kinetics_id})
        with Session(committed_engine) as selecting:
            selecting.execute(text("SET LOCAL lock_timeout = '300ms'"))
            with pytest.raises(DBAPIError) as raised:
                lock_record_for_selection(
                    selecting, record_type=SubmissionRecordType.kinetics, record_id=kinetics_id
                )
            assert getattr(raised.value.orig, "sqlstate", None) == "55P03"
            selecting.rollback()
