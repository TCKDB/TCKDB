"""The migration must preserve undeclared legacy science without a data step."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.base import Base
from app.db.models.common import SubmissionRecordType
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.services.scientific_read._factories import make_species, make_species_entry, next_inchi_key


@pytest.mark.parametrize("approved", [False, True])
def test_migration_preserves_legacy_null_and_enforces_new_scalars(db_session, monkeypatch, approved):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/e7b1c9d4a632_declare_thermo_enthalpy_reference.py"
    spec = spec_from_file_location("enthalpy_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db_session.connection(), opts={"target_metadata": Base.metadata})))
    species = make_species(db_session, inchi_key=next_inchi_key("ENTHMIG"))
    entry = make_species_entry(db_session, species)
    # Transactional DDL: rollback by the fixture restores the original schema.
    old_id = db_session.scalar(text(
        "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, h298_kj_mol, enthalpy_reference_kind) "
        "VALUES (:ref, :entry, 'computed', 42, 'formation_298k') RETURNING id"
    ), {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]})
    if approved:
        _approve(db_session, SubmissionRecordType.thermo, old_id, _actor(db_session))
    migration.downgrade()
    migration.upgrade()
    row = db_session.execute(text(
        "SELECT h298_kj_mol, enthalpy_reference_kind FROM thermo WHERE id=:id"
    ), {"id": old_id}).one()
    assert tuple(row) == (42, None)
    # Enforced by a trigger, not a CHECK -- see e7b1c9d4a632's docstring for
    # why a CHECK (even NOT VALID) would freeze this legacy row instead of
    # merely leaving it undeclared.
    assert db_session.scalar(text(
        "SELECT count(*) FROM pg_trigger "
        "WHERE tgname = 'trg_guard_thermo_enthalpy_reference' AND tgrelid = 'public.thermo'::regclass"
    )) == 1
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, h298_kj_mol) "
            "VALUES (:ref, :entry, 'computed', 0)"
        ), {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]})
    db_session.execute(text(
        "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, enthalpy_reference_kind) "
        "VALUES (:ref, :entry, 'computed', 'formation_298k')"
    ), {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]})
    migration.downgrade()
    assert db_session.scalar(text("SELECT count(*) FROM pg_type WHERE typname='enthalpy_reference_kind'")) == 0
    migration.upgrade()


def _legacy_undeclared_thermo_id(db_session) -> int:
    """A row that already violates the rule -- h298 set, no declaration.

    Only reachable by disabling the guard trigger for the insert: the ORM
    model no longer carries a CheckConstraint for this rule (the trigger
    enforces it), the trigger itself refuses this on INSERT same as it
    would on UPDATE, and the workflow layer refuses to build such a
    request at all. This models a row that predates the declaration and
    was never backfilled -- disabling/re-enabling a trigger is
    transactional, so the test fixture's rollback undoes it same as any
    other write.
    """
    species = make_species(db_session, inchi_key=next_inchi_key("ENTHLEGACY"))
    entry = make_species_entry(db_session, species)
    db_session.execute(text("ALTER TABLE thermo DISABLE TRIGGER trg_guard_thermo_enthalpy_reference"))
    try:
        return db_session.scalar(text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, h298_kj_mol) "
            "VALUES (:ref, :entry, 'computed', -74.5) RETURNING id"
        ), {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]})
    finally:
        db_session.execute(text("ALTER TABLE thermo ENABLE TRIGGER trg_guard_thermo_enthalpy_reference"))


def test_insert_with_scalar_and_no_declaration_is_refused(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("ENTHINS"))
    entry = make_species_entry(db_session, species)
    with pytest.raises(DBAPIError, match="requires enthalpy_reference_kind") as caught, db_session.begin_nested():
        db_session.execute(text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, h298_kj_mol) "
            "VALUES (:ref, :entry, 'computed', 10)"
        ), {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]})
    assert "thermo.h298_kj_mol requires enthalpy_reference_kind to be declared" in str(caught.value.orig)


def test_update_that_sets_scalar_without_declaration_is_refused(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("ENTHUPDNODECL"))
    entry = make_species_entry(db_session, species)
    thermo_id = db_session.scalar(text(
        "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin) "
        "VALUES (:ref, :entry, 'computed') RETURNING id"
    ), {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]})
    with pytest.raises(DBAPIError, match="requires enthalpy_reference_kind"), db_session.begin_nested():
        db_session.execute(text(
            "UPDATE thermo SET h298_kj_mol = 10 WHERE id = :id"
        ), {"id": thermo_id})


def test_update_to_unrelated_column_on_legacy_undeclared_row_succeeds(db_session):
    thermo_id = _legacy_undeclared_thermo_id(db_session)
    db_session.execute(text("UPDATE thermo SET note = 'reviewed by curator' WHERE id = :id"), {"id": thermo_id})
    db_session.flush()
    row = db_session.execute(text(
        "SELECT note, h298_kj_mol, enthalpy_reference_kind FROM thermo WHERE id = :id"
    ), {"id": thermo_id}).one()
    assert tuple(row) == ("reviewed by curator", -74.5, None)


def test_update_adding_a_declaration_to_a_legacy_row_succeeds(db_session):
    thermo_id = _legacy_undeclared_thermo_id(db_session)
    db_session.execute(text(
        "UPDATE thermo SET enthalpy_reference_kind = 'formation_298k' WHERE id = :id"
    ), {"id": thermo_id})
    db_session.flush()
    row = db_session.execute(text(
        "SELECT h298_kj_mol, enthalpy_reference_kind FROM thermo WHERE id = :id"
    ), {"id": thermo_id}).one()
    assert tuple(row) == (-74.5, "formation_298k")
