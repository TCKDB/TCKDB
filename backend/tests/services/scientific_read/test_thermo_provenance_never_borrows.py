"""A thermo's provenance and evidence come only from what it links to (#645).

Owner decision, 2026-10-01: never borrow. A thermo shows the statmech and the
calculations it actually links to -- its own ``statmech_id`` and its own source
calculations -- and nothing else. With neither, those fields are absent and the
evidence checklist/score count nothing for them. There is no exception for
legacy rows.

Before the fix, ``get_species_thermo`` fell back to the lowest statmech id of
the species entry, so an unlinked (say experimental) thermo displayed an
unrelated statmech's ref, primary calculation and level of theory, and scored
exactly like a linked computed record (which feeds the default sort and
``collapse=first``).
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    SCFStabilityStatus,
    ScientificOriginKind,
    StatmechCalculationRole,
    ThermoCalculationRole,
)
from app.db.models.statmech import StatmechSourceCalculation
from app.db.models.thermo import ThermoSourceCalculation
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.services.scientific_read.thermo import get_species_thermo
from tests.services.scientific_read._factories import (
    attach_geometry_validation,
    attach_scf_stability,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)
from tests.services.scientific_read.test_get_species_thermo import _add_statmech_with_freq_sp


def _entry(db_session):
    species = make_species(db_session, smiles="C#CCNCNCCCCC", inchi_key=next_inchi_key("NB"))
    return make_species_entry(db_session, species)


def _records(db_session, entry, **request):
    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest(**request)
    )
    return {r.thermo_ref: r for r in response.records}


def _linked_and_unlinked(db_session):
    """One statmech; a linked computed thermo first, then an unlinked
    experimental one (newer, so it wins any score tie)."""
    entry = _entry(db_session)
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    statmech, freq, sp = _add_statmech_with_freq_sp(db_session, entry, lot)
    linked = make_thermo_scalar(db_session, species_entry=entry, statmech_id=statmech.id)
    unlinked = make_thermo_scalar(
        db_session, species_entry=entry, scientific_origin=ScientificOriginKind.experimental
    )
    assert unlinked.statmech_id is None
    return entry, lot, statmech, freq, sp, linked, unlinked


def test_unlinked_thermo_shows_no_statmech_or_calculations(db_session):
    """Reproduces #645: the sibling statmech's ref, calcs and level showed here."""
    entry, _lot, _stat, _freq, _sp, _linked, unlinked = _linked_and_unlinked(db_session)

    prov = _records(db_session, entry)[unlinked.public_ref].provenance

    assert prov.statmech_id is None
    assert prov.statmech_ref is None
    assert prov.primary_calculation is None
    assert prov.level_of_theory is None
    assert prov.freq_calculation_id is None
    assert prov.sp_calculation_id is None
    assert prov.conformer_observation_id is None


def test_unlinked_thermo_evidence_counts_only_its_own_links(db_session):
    """The sibling statmech has freq, sp and opt sources, a passed geometry
    validation on the opt and a stable SCF on the sp, so every predicate that
    could borrow has something to borrow. The unlinked record gets none."""
    entry, lot, statmech, _freq, sp, linked, unlinked = _linked_and_unlinked(db_session)
    opt = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id, lot_id=lot.id
    )
    db_session.add(
        StatmechSourceCalculation(
            statmech_id=statmech.id, calculation_id=opt.id, role=StatmechCalculationRole.opt
        )
    )
    attach_geometry_validation(db_session, calculation=opt)
    attach_scf_stability(db_session, calculation=sp, status=SCFStabilityStatus.stable)
    db_session.flush()

    records = _records(db_session, entry)
    bare = records[unlinked.public_ref].evidence_completeness
    full = records[linked.public_ref].evidence_completeness

    assert bare.checklist == {
        "has_source_calculations": False,
        "has_statmech_source": False,
        "has_frequency_evidence": False,
        "has_sp_or_energy_evidence": False,
        "has_temperature_dependent_model": False,
        "has_uncertainty": False,
        "has_geometry_validation": False,
        "has_scf_stability": False,
    }
    assert bare.score == 0
    # The linked record does count the same sibling's evidence.
    assert full.checklist["has_geometry_validation"] is True
    assert full.checklist["has_scf_stability"] is True
    assert full.score == 6


def test_collapse_first_prefers_the_linked_record(db_session):
    """The unlinked record is newer, so it only loses on evidence score."""
    entry, _lot, _stat, _freq, _sp, linked, _unlinked = _linked_and_unlinked(db_session)

    response = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(collapse=CollapseMode.first),
    )

    assert [r.thermo_ref for r in response.records] == [linked.public_ref]


def test_linked_thermo_shows_its_own_statmech(db_session):
    entry, lot, statmech, freq, sp, linked, _unlinked = _linked_and_unlinked(db_session)

    rec = _records(db_session, entry)[linked.public_ref]

    assert rec.provenance.statmech_id == statmech.id
    assert rec.provenance.statmech_ref == statmech.public_ref
    assert rec.provenance.freq_calculation_id == freq.id
    assert rec.provenance.sp_calculation_id == sp.id
    assert rec.provenance.level_of_theory.level_of_theory_ref == lot.public_ref
    checklist = rec.evidence_completeness.checklist
    assert checklist["has_statmech_source"] is True
    assert checklist["has_source_calculations"] is True
    assert checklist["has_frequency_evidence"] is True


def test_a_records_own_source_calculations_count_without_a_statmech(db_session):
    entry = _entry(db_session)
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    _add_statmech_with_freq_sp(db_session, entry, lot)  # a sibling it must not borrow
    own_lot = make_lot(db_session, method="ccsdt", basis="ccpvtz")
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    sp = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=own_lot.id
    )
    db_session.add(
        ThermoSourceCalculation(
            thermo_id=thermo.id, calculation_id=sp.id, role=ThermoCalculationRole.sp
        )
    )
    db_session.flush()

    rec = _records(db_session, entry)[thermo.public_ref]

    assert rec.provenance.statmech_ref is None
    assert rec.provenance.sp_calculation_id == sp.id
    assert rec.provenance.freq_calculation_id is None
    assert rec.provenance.level_of_theory.level_of_theory_ref == own_lot.public_ref
    checklist = rec.evidence_completeness.checklist
    assert checklist["has_source_calculations"] is True
    assert checklist["has_sp_or_energy_evidence"] is True
    assert checklist["has_frequency_evidence"] is False
    assert checklist["has_statmech_source"] is False


def test_linked_to_the_higher_id_statmech_does_not_show_the_lower(db_session):
    entry = _entry(db_session)
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="def2svp")
    stat_a, _fa, _sa = _add_statmech_with_freq_sp(db_session, entry, lot_a)
    stat_b, freq_b, _sb = _add_statmech_with_freq_sp(db_session, entry, lot_b)
    thermo = make_thermo_scalar(db_session, species_entry=entry, statmech_id=stat_b.id)

    prov = _records(db_session, entry)[thermo.public_ref].provenance

    assert prov.statmech_ref == stat_b.public_ref != stat_a.public_ref
    assert prov.freq_calculation_id == freq_b.id
    assert prov.level_of_theory.level_of_theory_ref == lot_b.public_ref
