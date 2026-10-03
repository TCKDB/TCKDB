"""The consistency context hash ignores a thermo's declared energy level (#619).

``snapshot()`` hashes every ORM column, and ``thermo_inputs()`` feeds the D1-D6
checks and the external Cp comparison. Adding ``thermo.energy_level_of_theory_id``
would have changed every thermo context hash and restaled every stored
consistency review on deploy, although no check reads the column. It is excluded.

These tests pin both halves: the column is out of the snapshot, and every other
thermo column is still in it (so the exclusion cannot quietly widen).
"""

from __future__ import annotations

from sqlalchemy import inspect

from app.db.models.thermo import Thermo
from app.services.consistency.core import (
    THERMO_HASH_EXCLUDED_COLUMNS,
    encoded,
    thermo_inputs,
)
from tests.services.scientific_read._factories import (
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
)


def test_a_declared_energy_level_does_not_change_the_thermo_inputs(db_session) -> None:
    entry = make_species_entry(db_session, make_species(db_session, smiles="[CH3]", charge=0, multiplicity=2))
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    db_session.flush()
    before = encoded(thermo_inputs(thermo))

    thermo.energy_level_of_theory_id = make_lot(db_session, method="b3lyp", basis="def2svp").id
    db_session.flush()

    assert encoded(thermo_inputs(thermo)) == before
    assert "energy_level_of_theory_id" not in thermo_inputs(thermo)


def test_only_the_declared_level_is_excluded_and_everything_else_is_hashed(db_session) -> None:
    assert THERMO_HASH_EXCLUDED_COLUMNS == {"energy_level_of_theory_id"}
    entry = make_species_entry(db_session, make_species(db_session, smiles="[CH3]", charge=0, multiplicity=2))
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    db_session.flush()
    hashed = set(thermo_inputs(thermo))
    clocks = {"created_at", "updated_at", "created_by"}
    # Declarations a row did not make stay out of the digest (snapshot_defaults),
    # so the three declaration columns are absent from an undeclared row's.
    undeclared = {"thermodynamic_target_kind", "target_conformer_group_id", "protocol_declaration"}
    columns = {c.key for c in inspect(Thermo).columns} - clocks - THERMO_HASH_EXCLUDED_COLUMNS - undeclared
    assert columns <= hashed, sorted(columns - hashed)
    assert not (undeclared & hashed)
    # A value column that does feed the checks still moves the hash.
    before = encoded(thermo_inputs(thermo))
    thermo.h298_kj_mol = (thermo.h298_kj_mol or 0.0) + 1.0
    db_session.flush()
    assert encoded(thermo_inputs(thermo)) != before
