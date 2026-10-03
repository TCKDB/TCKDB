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

from app.db.models.common import ThermoTargetKind
from app.db.models.thermo import Thermo
from app.services.consistency.core import (
    THERMO_HASH_EXCLUDED_COLUMNS,
    encoded,
    thermo_inputs,
)
from app.services.reproducibility_rubric import _mapped_columns
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


DECLARATIONS = {"thermodynamic_target_kind", "target_conformer_group_id", "protocol_declaration"}


def test_only_depositor_declarations_are_excluded_and_everything_else_is_hashed(db_session) -> None:
    assert THERMO_HASH_EXCLUDED_COLUMNS == {"energy_level_of_theory_id"} | DECLARATIONS
    entry = make_species_entry(db_session, make_species(db_session, smiles="[CH3]", charge=0, multiplicity=2))
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    db_session.flush()
    hashed = set(thermo_inputs(thermo))
    clocks = {"created_at", "updated_at", "created_by"}
    columns = {c.key for c in inspect(Thermo).columns} - clocks - THERMO_HASH_EXCLUDED_COLUMNS
    assert columns <= hashed, sorted(columns - hashed)
    # A value column that does feed the checks still moves the hash.
    before = encoded(thermo_inputs(thermo))
    thermo.h298_kj_mol = (thermo.h298_kj_mol or 0.0) + 1.0
    db_session.flush()
    assert encoded(thermo_inputs(thermo)) != before


def test_a_target_or_protocol_does_not_restale_a_consistency_review_but_does_change_the_reproducibility_snapshot(
    db_session,
) -> None:
    """No consistency check reads a declaration (#619/#633 precedent); the reproducibility snapshot does include it."""
    entry = make_species_entry(db_session, make_species(db_session, smiles="[CH3]", charge=0, multiplicity=2))
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    db_session.flush()
    consistency_before = encoded(thermo_inputs(thermo))
    reproducibility_before = encoded(_mapped_columns(thermo))

    thermo.thermodynamic_target_kind = ThermoTargetKind.equilibrium_ensemble
    thermo.protocol_declaration = {"version": 1, "recipe": {"name": "g4"}}
    db_session.flush()

    assert encoded(thermo_inputs(thermo)) == consistency_before
    assert DECLARATIONS.isdisjoint(thermo_inputs(thermo))
    assert encoded(_mapped_columns(thermo)) != reproducibility_before
    assert {"thermodynamic_target_kind", "protocol_declaration"} <= set(_mapped_columns(thermo))
