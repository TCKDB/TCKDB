"""Keep the approved-row immutability probe after introducing declarations.

The original pre-change probe and its passing result are recorded in the
verification report; its fixture now supplies the required declaration.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.common import SubmissionRecordType
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)


def test_declared_thermo_remains_immutable_after_approval(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("ENTHREF"))
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    assert thermo.h298_kj_mol == -12.3
    _approve(db_session, SubmissionRecordType.thermo, thermo.id, _actor(db_session))
    with pytest.raises(DBAPIError, match="immutable"), db_session.begin_nested():
        db_session.execute(
            text("UPDATE thermo SET h298_kj_mol = 42 WHERE id = :id"),
            {"id": thermo.id},
        )
