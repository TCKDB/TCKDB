"""An ``imaginary_mode`` record is held against the frequency result it cites.

The record states what a frequency calculation found; the calculation's own
stored result is the evidence. These run at the persistence seam because the
stored result must be shaped exactly (an ``n_imag`` of 2 with and without a
designated reaction coordinate) and the request schemas cannot produce every
shape a stored result can have.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
)

from app.db.models.common import CalculationType
from app.services.transition_state_validation import (
    persist_transition_state_validation_evidence,
)
from tests.services.scientific_read._factories import (
    attach_freq_result,
    make_calculation,
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)


def _ts(db_session, tag: str):
    reactant = make_species(db_session, inchi_key=next_inchi_key(f"{tag}R"))
    product = make_species(db_session, inchi_key=next_inchi_key(f"{tag}P"))
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(db_session, reactants=[reactant], products=[product]),
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    return reaction_entry, make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=reaction_entry),
    )


def _freq(db_session, ts_entry, frequencies, **kwargs):
    calc = make_calculation(
        db_session, transition_state_entry_id=ts_entry.id, type=CalculationType.freq
    )
    attach_freq_result(db_session, calculation=calc, frequencies_cm1=frequencies, **kwargs)
    return calc


def _persist(db_session, reaction_entry, ts_entry, calc, **record):
    fields = {"kind": "imaginary_mode", "passed": True, "rationale": "r", **record}
    return persist_transition_state_validation_evidence(
        db_session,
        [TransitionStateValidationEvidenceIn(**fields)],
        transition_state_entry_id=ts_entry.id,
        reconstruction_calculation_ids=[calc.id],
        subject_label="ts",
        field_path="validation_evidence",
        reaction_entry_id=reaction_entry.id,
        transition_state_geometry_id=None,
    )


def test_a_record_that_agrees_with_its_frequency_result_is_stored(db_session) -> None:
    rxn, ts = _ts(db_session, "IMAGOK")
    calc = _freq(db_session, ts, [-1500.0, 100.0, 200.0])
    (row,) = _persist(
        db_session, rxn, ts, calc, imaginary_frequency_count=1, imaginary_frequency_cm1=-1500.4
    )
    assert row.imaginary_frequency_count == 1


def test_a_stated_count_that_contradicts_the_result_is_refused(db_session) -> None:
    rxn, ts = _ts(db_session, "IMAGCNT")
    calc = _freq(db_session, ts, [-1500.0, 100.0])
    with pytest.raises(ValueError, match="states 2 imaginary mode.*recorded 1"):
        _persist(db_session, rxn, ts, calc, imaginary_frequency_count=2)


def test_a_stated_frequency_that_contradicts_the_result_is_refused(db_session) -> None:
    rxn, ts = _ts(db_session, "IMAGVAL")
    calc = _freq(db_session, ts, [-1500.0, 100.0])
    with pytest.raises(ValueError, match="-900.0 cm.*recorded -1500.0"):
        _persist(db_session, rxn, ts, calc, imaginary_frequency_cm1=-900.0)


def test_nothing_stated_is_nothing_contradicted(db_session) -> None:
    rxn, ts = _ts(db_session, "IMAGNONE")
    calc = _freq(db_session, ts, [-1500.0, 100.0])
    _persist(db_session, rxn, ts, calc)


def test_a_pass_with_two_modes_needs_a_designated_reaction_coordinate(db_session) -> None:
    rxn, ts = _ts(db_session, "IMAGTWO")
    calc = _freq(db_session, ts, [-1500.0, -40.0, 100.0])
    with pytest.raises(ValueError, match="does not designate which one is the reaction coordinate"):
        _persist(db_session, rxn, ts, calc, imaginary_frequency_count=2)
    # The count may also be left for the stored result to supply.
    with pytest.raises(ValueError, match="does not designate"):
        _persist(db_session, rxn, ts, calc)


def test_two_modes_with_a_designation_are_accepted_not_refused_as_not_one(db_session) -> None:
    """ADR 0012 deliberately allows extra (torsional) imaginary modes.

    This is the test that goes red if someone "tightens" the rule to demand
    exactly one imaginary mode: the stored result designates mode 1, so a pass
    with two is what a correct deposit looks like.
    """
    rxn, ts = _ts(db_session, "IMAGDES")
    calc = _freq(db_session, ts, [-1500.0, -40.0, 100.0], reaction_coordinate_mode_index=1)
    (row,) = _persist(db_session, rxn, ts, calc, imaginary_frequency_count=2)
    assert row.imaginary_frequency_count == 2


def test_a_failed_record_with_two_undesignated_modes_is_stored(db_session) -> None:
    rxn, ts = _ts(db_session, "IMAGFAIL")
    calc = _freq(db_session, ts, [-1500.0, -40.0, 100.0])
    (row,) = _persist(db_session, rxn, ts, calc, passed=False, imaginary_frequency_count=2)
    assert row.passed is False


def test_the_frequency_comparison_is_by_magnitude_whatever_sign_was_stored(db_session) -> None:
    """``imag_freq_cm1`` has no sign rule; the house reads it as a magnitude."""
    rxn, ts = _ts(db_session, "IMAGSIGN")
    calc = _freq(db_session, ts, [-1500.0, 100.0])
    calc.freq_result.imag_freq_cm1 = 1500.0
    db_session.flush()
    # Agrees in magnitude with the stored +1500, so it is not refused ...
    _persist(db_session, rxn, ts, calc, imaginary_frequency_cm1=-1500.0)
    # ... and a different magnitude still is, whichever sign is stored.
    with pytest.raises(ValueError, match="recorded 1500.0"):
        _persist(db_session, rxn, ts, calc, imaginary_frequency_cm1=-900.0)
