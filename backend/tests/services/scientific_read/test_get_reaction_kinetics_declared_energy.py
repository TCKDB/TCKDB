"""``levels.declared_energy`` on the kinetics read: the record's own claim, never borrowed.

The derived ``levels.energy`` comes from the linked TS single point; the declared level is
the stored ``kinetics.energy_level_of_theory_id``. They are separate answers: a record can
carry either without the other, and neither is ever filled from the other or from a sibling.
"""

from __future__ import annotations

from app.db.models.level_of_theory import LevelOfTheoryMerge
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.services.scientific_read.kinetics import get_reaction_kinetics
from tests.services.scientific_read._factories import make_kinetics, make_lot
from tests.services.scientific_read.test_get_reaction_kinetics import _ts_backed_kinetics


def _read(db_session, entry):
    return get_reaction_kinetics(db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()).records


def _chain(db_session):
    opt = make_lot(db_session, method="b3lyp", basis="6-31g")
    sp = make_lot(db_session, method="ccsd(t)", basis="cc-pvtz")
    return sp, _ts_backed_kinetics(db_session, opt_lot=opt, freq_lot=opt, sp_lot=sp)


def test_a_derived_energy_level_is_not_reported_as_a_declaration(db_session):
    """Kills: read fills the declaration from the linked single point."""
    sp, (entry, _kinetics, *_rest) = _chain(db_session)

    (record,) = _read(db_session, entry)

    assert record.levels.energy.level_of_theory_id == sp.id
    assert record.levels.declared_energy is None


def test_the_declaration_is_reported_apart_from_the_derived_level(db_session):
    sp, (entry, kinetics, *_rest) = _chain(db_session)
    declared = make_lot(db_session, method="xyg3", basis="6-311+g(3df,2p)")
    kinetics.energy_level_of_theory_id = declared.id
    db_session.flush()

    (record,) = _read(db_session, entry)

    assert record.levels.declared_energy.level_of_theory_id == declared.id
    assert record.levels.energy.level_of_theory_id == sp.id


def test_a_declaration_with_no_linked_energy_is_reported_and_derives_nothing(db_session):
    from tests.services.scientific_read.test_get_reaction_kinetics import _setup_entry

    entry = _setup_entry(db_session)
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    declared = make_lot(db_session, method="xyg3", basis="6-311+g(3df,2p)")
    kinetics.energy_level_of_theory_id = declared.id
    db_session.flush()

    (record,) = _read(db_session, entry)

    assert record.levels.declared_energy.level_of_theory_id == declared.id
    assert record.levels.energy is None


def test_a_sibling_records_declaration_is_not_lent(db_session):
    from tests.services.scientific_read.test_get_reaction_kinetics import _setup_entry

    entry = _setup_entry(db_session)
    declared = make_lot(db_session, method="xyg3", basis="6-311+g(3df,2p)")
    sibling = make_kinetics(db_session, reaction_entry=entry)
    sibling.energy_level_of_theory_id = declared.id
    plain = make_kinetics(db_session, reaction_entry=entry, a=2.0e-12)
    db_session.flush()

    by_ref = {r.kinetics_ref: r for r in _read(db_session, entry)}

    assert by_ref[sibling.public_ref].levels.declared_energy is not None
    assert by_ref[plain.public_ref].levels.declared_energy is None


def test_a_declared_level_later_merged_reads_as_the_row_it_merged_into(db_session):
    from tests.services.scientific_read.test_get_reaction_kinetics import _setup_entry

    entry = _setup_entry(db_session)
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    duplicate = make_lot(db_session, method="b3lyp", basis="def2svp")
    holder = make_lot(db_session, method="b3lyp", basis="def2-svp")
    kinetics.energy_level_of_theory_id = duplicate.id
    db_session.flush()
    db_session.add(LevelOfTheoryMerge(merged_lot_id=duplicate.id, into_lot_id=holder.id))
    db_session.flush()

    (record,) = _read(db_session, entry)

    assert record.levels.declared_energy.level_of_theory_ref == holder.public_ref
