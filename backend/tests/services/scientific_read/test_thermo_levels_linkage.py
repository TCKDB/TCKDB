"""A thermo's derived ``levels`` come only from what the thermo itself owns (#636).

``get_species_thermo`` resolves a basis statmech per record. Provenance and
evidence still fall back to the lowest statmech id of the entry for display,
but the derived ``levels.*`` (geometry / frequency / energy) must not: an
experimental thermo, or a computed one with its own source calculations, would
report the levels of an unrelated statmech on the same entry.

The rule, from the thermo's side:

* its own source calculations, per role;
* then the statmech it is linked to by ``thermo.statmech_id``, per role;
* otherwise absent (``None``), never borrowed.
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    ScientificOriginKind,
    ThermoCalculationRole,
)
from app.db.models.thermo import ThermoSourceCalculation
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.services.scientific_read.thermo import get_species_thermo
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)
from tests.services.scientific_read.test_get_species_thermo import _add_statmech_with_freq_sp


def _entry(db_session):
    species = make_species(db_session, smiles="C#CCNCNCCCCC", inchi_key=next_inchi_key("TL"))
    return make_species_entry(db_session, species)


def _levels_by_thermo(db_session, entry):
    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    return {r.thermo_ref: r.levels for r in response.records}


def _refs(levels):
    return tuple(
        None if level is None else level.level_of_theory_ref
        for level in (levels.geometry, levels.frequency, levels.energy)
    )


def test_an_unlinked_experimental_thermo_reports_no_levels(db_session):
    """Reproduces #636: the sibling statmech's levels used to appear here."""
    entry = _entry(db_session)
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    _add_statmech_with_freq_sp(db_session, entry, lot)
    experimental = make_thermo_scalar(
        db_session, species_entry=entry, scientific_origin=ScientificOriginKind.experimental
    )
    assert experimental.statmech_id is None

    levels = _levels_by_thermo(db_session, entry)[experimental.public_ref]

    assert _refs(levels) == (None, None, None)
    assert levels.energy_source is None


def test_a_linked_thermo_inherits_its_own_statmechs_levels(db_session):
    entry = _entry(db_session)
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="def2svp")
    # The lower id is the entry-wide pick; the thermo is linked to the other.
    _stat_a, _fa, _sa = _add_statmech_with_freq_sp(db_session, entry, lot_a)
    stat_b, _fb, _sb = _add_statmech_with_freq_sp(db_session, entry, lot_b)
    linked = make_thermo_scalar(db_session, species_entry=entry, statmech_id=stat_b.id)

    levels = _levels_by_thermo(db_session, entry)[linked.public_ref]

    assert levels.frequency.level_of_theory_ref == lot_b.public_ref
    assert levels.energy.level_of_theory_ref == lot_b.public_ref


def test_a_thermos_own_sources_win_over_its_statmech_and_never_borrow(db_session):
    entry = _entry(db_session)
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_own = make_lot(db_session, method="ccsdt", basis="ccpvtz")
    stat, _freq, _sp = _add_statmech_with_freq_sp(db_session, entry, lot_a)

    def thermo_with_own_sp(statmech_id):
        thermo = make_thermo_scalar(db_session, species_entry=entry, statmech_id=statmech_id)
        calc = make_calculation(
            db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot_own.id
        )
        db_session.add(
            ThermoSourceCalculation(
                thermo_id=thermo.id, calculation_id=calc.id, role=ThermoCalculationRole.sp
            )
        )
        db_session.flush()
        return thermo

    unlinked = thermo_with_own_sp(None)
    linked = thermo_with_own_sp(stat.id)

    by_ref = _levels_by_thermo(db_session, entry)
    # Own sp wins the energy role. Unlinked: nothing else is borrowed.
    assert by_ref[unlinked.public_ref].energy.level_of_theory_ref == lot_own.public_ref
    assert by_ref[unlinked.public_ref].frequency is None
    # Linked: the roles the thermo does not cover come from its statmech.
    assert by_ref[linked.public_ref].energy.level_of_theory_ref == lot_own.public_ref
    assert by_ref[linked.public_ref].frequency.level_of_theory_ref == lot_a.public_ref
