"""Regression tests for the scientific_read test factories themselves.

These guard invariants the factories must keep so the rest of the
scientific test suite stays deterministic — in particular that rapid
successive ``make_chem_reaction`` calls do not collide on
``ChemReaction.public_ref``.
"""

from __future__ import annotations

from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_species,
    next_inchi_key,
)


def test_make_chem_reaction_populates_stoichiometry_hash(db_session):
    """Factory mirrors production by computing ``stoichiometry_hash``
    so the public-ref listener takes the content-derived path instead
    of the id()-based fallback."""
    a = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("FHA"))
    b = make_species(db_session, smiles="O", inchi_key=next_inchi_key("FHB"))
    rxn = make_chem_reaction(db_session, reactants=[a], products=[b])
    assert rxn.stoichiometry_hash is not None
    assert len(rxn.stoichiometry_hash) == 64


def test_many_chem_reactions_get_distinct_public_refs(db_session):
    """Creating many ChemReaction rows through the factory in one
    transaction must not collide on ``public_ref``.

    Regression for a flake where the public-ref fallback used
    ``id(obj)`` whenever ``stoichiometry_hash`` was unset; CPython
    recycles memory addresses, so two successive factory instances
    occasionally hashed to the same canonical string and tripped the
    ``ix_chem_reaction_public_ref`` unique index.
    """
    refs: set[str] = set()
    hashes: set[str] = set()
    for i in range(20):
        rs = make_species(
            db_session, smiles="C", inchi_key=next_inchi_key(f"DR{i}")
        )
        ps = make_species(
            db_session, smiles="O", inchi_key=next_inchi_key(f"DP{i}")
        )
        rxn = make_chem_reaction(db_session, reactants=[rs], products=[ps])
        refs.add(rxn.public_ref)
        hashes.add(rxn.stoichiometry_hash)
    assert len(refs) == 20
    assert len(hashes) == 20
