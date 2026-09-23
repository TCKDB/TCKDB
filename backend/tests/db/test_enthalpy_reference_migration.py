"""The migration must preserve undeclared legacy science without a data step."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
        "VALUES (:ref, :entry, 'computed', 42, 'formation_from_elements_298k') RETURNING id"
    ), {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]})
    if approved:
        _approve(db_session, SubmissionRecordType.thermo, old_id, _actor(db_session))
    migration.downgrade()
    migration.upgrade()
    row = db_session.execute(text(
        "SELECT h298_kj_mol, enthalpy_reference_kind FROM thermo WHERE id=:id"
    ), {"id": old_id}).one()
    assert tuple(row) == (42, None)
    assert db_session.scalar(text(
        "SELECT convalidated FROM pg_constraint WHERE conname='ck_thermo_h298_requires_enthalpy_reference'"
    )) is False
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, h298_kj_mol) "
            "VALUES (:ref, :entry, 'computed', 0)"
        ), {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]})
    db_session.execute(text(
        "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, enthalpy_reference_kind) "
        "VALUES (:ref, :entry, 'computed', 'formation_from_elements_298k')"
    ), {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]})
    migration.downgrade()
    assert db_session.scalar(text("SELECT count(*) FROM pg_type WHERE typname='enthalpy_reference_kind'")) == 0
    migration.upgrade()
