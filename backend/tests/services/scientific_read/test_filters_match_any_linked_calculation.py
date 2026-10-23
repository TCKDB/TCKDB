"""A level-of-theory or software filter keeps a record when ANY linked source calculation matches.

It used to look at one "primary" calculation chosen by a hidden role priority (sp, then composite, then freq, then opt for
thermo; ts_energy, then fit_source, then freq for kinetics), so a record whose frequency calculation ran at the requested
level was dropped whenever an sp calculation at another level outranked it. Each test names what it kills.
"""

from __future__ import annotations

from app.db.models.common import CalculationType, KineticsCalculationRole, ThermoCalculationRole
from app.db.models.kinetics import KineticsSourceCalculation
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.services.scientific_read.thermo import get_species_thermo
from tests.services.scientific_read._factories import (
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_software_release,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)


def _thermo_with_two_levels(db_session):
    """sp at level A (software gaussian) outranks freq at level B (software orca) in the old priority."""
    species = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("AL"))
    entry = make_species_entry(db_session, species)
    lot_a, lot_b = make_lot(db_session, method="a-method"), make_lot(db_session, method="b-method")
    gaussian = make_software_release(db_session, name="gaussian", version="16")
    orca = make_software_release(db_session, name="orca", version="6")
    sp = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot_a.id, software_release_id=gaussian.id
    )
    freq = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id, lot_id=lot_b.id, software_release_id=orca.id
    )
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=sp, role=ThermoCalculationRole.sp)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=freq, role=ThermoCalculationRole.freq)
    return entry, thermo, lot_a, lot_b


def _refs(response):
    return [r.thermo_ref for r in response.records]


def test_a_thermo_matches_the_level_of_its_frequency_calculation_not_only_its_sp(db_session):
    """Kills: the level filter reading only the role-priority primary (the sp), which hid this record at level B."""
    entry, thermo, lot_a, lot_b = _thermo_with_two_levels(db_session)

    for lot in (lot_a, lot_b):
        response = get_species_thermo(
            db_session, species_entry_id=entry.id, request=ThermoReadRequest(level_of_theory_id=lot.id)
        )
        assert _refs(response) == [thermo.public_ref], lot.method
    other = make_lot(db_session, method="c-method")
    none = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest(level_of_theory_id=other.id)
    )
    assert _refs(none) == []


def test_a_thermo_matches_the_software_of_any_linked_calculation(db_session):
    """Kills: the software filter reading only the primary calculation's software."""
    entry, thermo, *_ = _thermo_with_two_levels(db_session)

    for software in ("gaussian", "orca"):
        response = get_species_thermo(
            db_session, species_entry_id=entry.id, request=ThermoReadRequest(software=software)
        )
        assert _refs(response) == [thermo.public_ref], software
    assert _refs(
        get_species_thermo(db_session, species_entry_id=entry.id, request=ThermoReadRequest(software="molpro"))
    ) == []


def test_a_thermo_with_no_linked_calculation_matches_no_level_and_no_software(db_session):
    """Kills: a record with nothing linked matching a filter by luck of an empty comparison."""
    species = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("AN"))
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)
    lot = make_lot(db_session, method="z-method")

    assert get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest(level_of_theory_id=lot.id)
    ).records == []
    assert get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest(software="gaussian")
    ).records == []


def test_a_kinetics_record_matches_the_level_and_software_of_any_linked_calculation(db_session):
    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("KR"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("KP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    reactant_entry = make_species_entry(db_session, rs)
    entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[reactant_entry], product_entries=[make_species_entry(db_session, ps)]
    )
    lot_a, lot_b = make_lot(db_session, method="ka-method"), make_lot(db_session, method="kb-method")
    gaussian = make_software_release(db_session, name="gaussian", version="16")
    orca = make_software_release(db_session, name="orca", version="6")
    ts_energy = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=reactant_entry.id, lot_id=lot_a.id,
        software_release_id=gaussian.id,
    )
    freq = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=reactant_entry.id, lot_id=lot_b.id,
        software_release_id=orca.id,
    )
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    db_session.add_all(
        [
            KineticsSourceCalculation(
                kinetics_id=kinetics.id,
                calculation_id=ts_energy.id,
                role=KineticsCalculationRole.ts_energy,
            ),
            KineticsSourceCalculation(
                kinetics_id=kinetics.id,
                calculation_id=freq.id,
                role=KineticsCalculationRole.freq,
            ),
        ]
    )
    db_session.flush()

    def read(**kw):
        return [
            r.kinetics_ref
            for r in get_reaction_kinetics(
                db_session, reaction_entry_id=entry.id, request=KineticsReadRequest(**kw)
            ).records
        ]

    # The ts_energy calculation outranked the freq one in the old priority; both levels and both softwares match now.
    assert read(level_of_theory_id=lot_a.id) == [kinetics.public_ref]
    assert read(level_of_theory_id=lot_b.id) == [kinetics.public_ref]
    assert read(software="gaussian") == [kinetics.public_ref]
    assert read(software="orca") == [kinetics.public_ref]
    assert read(level_of_theory_id=make_lot(db_session, method="kc-method").id) == []
    assert read(software="molpro") == []
