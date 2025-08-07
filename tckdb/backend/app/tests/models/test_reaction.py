"""Tests for the Reaction model"""

from tckdb.backend.app.models.reaction import (
    Reaction,
    ReactionEntry,
    ReactionProduct,
    ReactionReactant,
)


def test_reaction_model():
    """Test creating a reaction with multiple participants"""
    rxn = Reaction(formal_charge=0, multiplicity=1, labels=["test"])
    rxn.reactant_assocs = [
        ReactionReactant(order_index=0, species_id=1),
        ReactionReactant(order_index=1, vdw_id=2),
    ]
    rxn.product_assocs = [ReactionProduct(order_index=0, species_id=3)]
    entry = ReactionEntry(reaction=rxn, kinetics={"A": 1.0})
    assert len(rxn.reactant_assocs) == 2
    assert rxn.reactant_assocs[0].species_id == 1
    assert rxn.product_assocs[0].species_id == 3
    assert rxn.entries[0] is entry
    assert str(rxn) == "<Reaction(id=None, charge=0)>"
