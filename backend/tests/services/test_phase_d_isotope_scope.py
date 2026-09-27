"""Isotope scope on rows the upload path actually persists.

The upload path strips isotope labels before storing ``species.smiles``
(``app.chemistry.species.canonical_species_identity``): isotopologues share
one ``species`` row, and the isotopic resolution lives only on the species
entry's ``isotope_key``. A scope rule that reads the species SMILES can
therefore never see an isotope on persisted data. Every entry here is made
by the real ``resolve_species_entry``, never hand-built, so these tests fail
if the rule stops reading the entry.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session

from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.consistency.kinetics import compare_kinetics
from app.services.consistency.stoichiometry import entry_facts, species_scope_reason
from app.services.species_resolution import resolve_species_entry
from tests.services.test_phase_d_numerical import _reaction


@contextmanager
def _rolled_back_session(db_engine):
    with Session(db_engine) as session:
        trans = session.begin()
        try:
            yield session
        finally:
            trans.rollback()


def _entry(session, smiles, charge=0, multiplicity=1):
    entry = resolve_species_entry(
        session, SpeciesEntryIdentityPayload(smiles=smiles, charge=charge, multiplicity=multiplicity))
    session.flush()
    return entry


CASES = [
    # (uploaded SMILES, charge, multiplicity, stored species SMILES, isotope key set, expected scope)
    ("C", 0, 1, "C", False, None),
    ("[2H]C([2H])([2H])[2H]", 0, 1, "C", True, "isotope_labelled_species_out_of_scope"),
    ("[13CH4]", 0, 1, "C", True, "isotope_labelled_species_out_of_scope"),
    ("[NH4+]", 1, 1, "[NH4+]", False, "charged_species_out_of_scope"),
]


def test_scope_reads_the_persisted_entry_not_the_stripped_species_smiles(db_engine):
    assert len(CASES) == 4
    with _rolled_back_session(db_engine) as session:
        for smiles, charge, multiplicity, stored, labelled, expected in CASES:
            entry = _entry(session, smiles, charge, multiplicity)
            # The premise: the stored species SMILES carries no isotope label.
            assert entry.species.smiles == stored
            assert (entry.isotope_key is not None) is labelled
            assert species_scope_reason(entry) == expected, smiles
            assert entry_facts(entry).has_isotopes is labelled


def _real_reaction(session, *, labelled):
    """H2 -> 2 H with every participant entry resolved through the upload path."""
    forward, reverse, mapping = _reaction()
    molecule, atom = ("[2H][2H]", "[2H]") if labelled else ("[H][H]", "[H]")
    resolved = {1: _entry(session, molecule), 2: _entry(session, atom, multiplicity=2)}
    for participant in forward.reaction_entry.structure_participants:
        entry = resolved[participant.species_entry_id]
        participant.species_entry_id, participant.species_entry = entry.id, entry
    rekeyed = {}
    for key, thermo in mapping.items():
        entry = resolved[key]
        thermo.species_entry_id, thermo.species_entry = entry.id, entry
        rekeyed[entry.id] = thermo
    return forward, reverse, rekeyed


@pytest.mark.parametrize("labelled,expected", [
    (False, None),
    (True, "isotope_specific_equilibrium_unsupported"),
])
def test_d3_refuses_an_isotope_labelled_participant_entry(db_engine, labelled, expected):
    with _rolled_back_session(db_engine) as session:
        forward, reverse, mapping = _real_reaction(session, labelled=labelled)
        assert all(t.species_entry.species.smiles in ("[H][H]", "[H]") for t in mapping.values())
        result = compare_kinetics(forward, reverse, mapping, temperature_grid=[500])
        rows = [json.loads(f.message) for f in result.findings if '"rate_units"' in f.message]
        assert len(rows) == 1
        assert rows[0]["reason"] == expected
        assert ("k_forward" in rows[0]) is (not labelled)
