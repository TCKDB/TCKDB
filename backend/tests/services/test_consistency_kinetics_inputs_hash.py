"""A kinetics record's declared energy level restales no stored review.

``compare_kinetics`` hashes every column of both rates it compares. No kinetics consistency
check reads ``energy_level_of_theory_id`` (a depositor's declaration, like thermo's, #619), so
declaring one must not change the context hash a stored review is judged against, and a rate
stored before the column existed must keep the hash it had (the pinned hashes in
``test_phase_d_d3_invariance`` pin the latter for every D3 scenario).
"""

from __future__ import annotations

from app.services.consistency.core import KINETICS_HASH_EXCLUDED_COLUMNS, encoded, snapshot
from app.services.consistency.kinetics import compare_kinetics
from app.services.reproducibility_rubric import _mapped_columns
from tests.services.scientific_read._factories import make_lot
from tests.services.test_phase_d_numerical import _reaction


def _context_hash(forward, reverse, mapping) -> str:
    return compare_kinetics(forward, reverse, mapping, temperature_grid=[500]).digest.context_hash


def test_a_declared_energy_level_does_not_change_the_consistency_context_hash(db_session) -> None:
    forward, reverse, mapping = _reaction()
    before = _context_hash(forward, reverse, mapping)

    forward.energy_level_of_theory_id = make_lot(db_session, method="b3lyp", basis="def2svp").id

    assert _context_hash(forward, reverse, mapping) == before


def test_only_the_energy_level_is_excluded_and_a_value_column_still_moves_the_hash() -> None:
    assert KINETICS_HASH_EXCLUDED_COLUMNS == {"energy_level_of_theory_id"}
    forward, reverse, mapping = _reaction()
    before = _context_hash(forward, reverse, mapping)
    forward.ea_kj_mol += 1.0
    assert _context_hash(forward, reverse, mapping) != before


def test_the_reproducibility_snapshot_reports_a_declaration_but_not_its_absence(db_session) -> None:
    """A row that declares nothing keeps its snapshot; one that declares a level differs."""
    forward, _reverse, _mapping = _reaction()
    assert "energy_level_of_theory_id" not in snapshot(forward)
    assert "energy_level_of_theory_id" not in _mapped_columns(forward)
    undeclared = encoded(_mapped_columns(forward))

    forward.energy_level_of_theory_id = make_lot(db_session, method="b3lyp", basis="def2svp").id

    assert encoded(_mapped_columns(forward)) != undeclared
