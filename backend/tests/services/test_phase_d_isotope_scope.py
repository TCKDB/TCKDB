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

from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.consistency.stoichiometry import entry_facts, species_scope_reason
from app.services.species_resolution import resolve_species_entry


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
