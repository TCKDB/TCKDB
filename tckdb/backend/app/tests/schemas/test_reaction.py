"""Tests for the Reaction schema"""

from tckdb.backend.app.schemas.reaction import ReactionBase


def test_reaction_schema():
    """Test creating a ReactionBase instance"""
    rxn = ReactionBase(
        formal_charge=0,
        multiplicity=1,
        reactant_species_ids=[1],
        product_species_ids=[2],
    )
    assert rxn.reactant_species_ids == [1]
    assert rxn.product_species_ids == [2]

    ReactionBase(formal_charge=0, multiplicity=1)
