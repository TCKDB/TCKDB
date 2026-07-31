"""A statmech record must be attributable to the subject it actually describes.

``statmech`` carries a strict XOR: either a species entry or a transition-state
entry owns it. The read layer used to project every record through a species
context, so a TS-owned partition function came back with an empty species
stand-in (``species_ref: ""``) and no mention of the transition state at all —
a fabricated attribution for a record whose real subject was unreachable.
"""

from __future__ import annotations

from app.db.models.statmech import Statmech
from app.services.scientific_read.statmech import get_statmech
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)


def _ts_entry(db_session):
    reactant = make_species(db_session, smiles="A", inchi_key=next_inchi_key("SA"))
    product = make_species(db_session, smiles="B", inchi_key=next_inchi_key("SB"))
    chem = make_chem_reaction(db_session, reactants=[reactant], products=[product])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    return entry, make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=entry),
    )


def test_ts_owned_statmech_reads_with_a_transition_state_subject(db_session):
    reaction_entry, ts_entry = _ts_entry(db_session)
    statmech = Statmech(
        species_entry_id=None,
        transition_state_entry_id=ts_entry.id,
        scientific_origin="computed",
    )
    db_session.add(statmech)
    db_session.flush()

    record = get_statmech(
        db_session, statmech_handle=statmech.public_ref
    ).record

    # No fabricated species context.
    assert record.species is None
    ts_context = record.transition_state
    assert ts_context is not None
    assert ts_context.transition_state_entry_ref == ts_entry.public_ref
    assert ts_context.transition_state_ref == ts_entry.transition_state.public_ref
    assert ts_context.charge == ts_entry.charge
    assert ts_context.multiplicity == ts_entry.multiplicity
    assert ts_context.reaction_entry_ref == reaction_entry.public_ref


def test_ts_owned_statmech_has_no_conformer_context(db_session):
    """Conformer groups hang off a species entry, so a TS record has none."""
    _reaction_entry, ts_entry = _ts_entry(db_session)
    statmech = Statmech(
        species_entry_id=None,
        transition_state_entry_id=ts_entry.id,
        scientific_origin="computed",
    )
    db_session.add(statmech)
    db_session.flush()

    record = get_statmech(
        db_session, statmech_handle=statmech.public_ref, include=["conformers"]
    ).record
    assert record.available_sections.has_conformers is False
    assert record.conformers == []


def test_species_owned_statmech_still_reads_a_species_subject(db_session):
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("SC"))
    species_entry = make_species_entry(db_session, species)
    statmech = make_statmech(db_session, species_entry=species_entry)

    record = get_statmech(
        db_session, statmech_handle=statmech.public_ref
    ).record
    assert record.transition_state is None
    assert record.species is not None
    assert record.species.species_entry_ref == species_entry.public_ref
    assert record.species.species_ref == species.public_ref
