"""The shared database world of the kinetics-selection tests: one reaction entry H + CH4 -> H2 + CH3."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_literature,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


@pytest.fixture
def world(db_session):
    h = make_species_entry(db_session, make_species(db_session, smiles="[H]", multiplicity=2, inchi_key=next_inchi_key("KSA")))
    ch4 = make_species_entry(db_session, make_species(db_session, smiles="C", multiplicity=1, inchi_key=next_inchi_key("KSB")))
    h2 = make_species_entry(db_session, make_species(db_session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("KSC")))
    ch3 = make_species_entry(db_session, make_species(db_session, smiles="[CH3]", multiplicity=2, inchi_key=next_inchi_key("KSD")))
    reaction = make_chem_reaction(db_session, reactants=[h.species, ch4.species], products=[h2.species, ch3.species])
    entry = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[h, ch4], product_entries=[h2, ch3])
    return SimpleNamespace(entry=entry, literature=make_literature(db_session), h=h, ch4=ch4)
