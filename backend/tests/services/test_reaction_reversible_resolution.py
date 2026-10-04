"""``inherit_reversible`` and ``reversible_twin``: the service half of #598, with no request schema in the way."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload
from tckdb_schemas.upload_warning import UploadWarning

from app.api.error_contract import CodedValueError
from app.services.reaction_resolution import (
    W_REACTION_REVERSIBLE_TWIN,
    compress_species_stoichiometry,
    inherit_reversible,
    resolve_chem_reaction,
    reversible_twin,
)
from app.services.species_resolution import resolve_species_entry
from app.workflows.reaction import CODE_REACTION_REVERSIBLE_REQUIRED, reversible_or_inherited
from tests.services.scientific_read._factories import make_species, make_species_entry, next_inchi_key

H = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
H2 = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}


@pytest.fixture
def sides(db_session):
    h = make_species_entry(db_session, make_species(db_session, smiles="[H]", multiplicity=2, inchi_key=next_inchi_key("RSH")))
    h2 = make_species_entry(db_session, make_species(db_session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("RSI")))
    return compress_species_stoichiometry([h, h]), compress_species_stoichiometry([h2])


def _store(db_session, sides, reversible: bool, warnings=None):
    reactants, products = sides
    return resolve_chem_reaction(
        db_session, reversible=reversible, reactant_stoichiometry=reactants, product_stoichiometry=products,
        warnings_out=warnings,
    )


def _inherited(db_session, sides):
    reactants, products = sides
    return inherit_reversible(db_session, reactant_stoichiometry=reactants, product_stoichiometry=products)


def test_nothing_stored_inherits_nothing(db_session, sides):
    assert _inherited(db_session, sides) is None


@pytest.mark.parametrize("stored", [True, False])
def test_exactly_one_stored_reaction_is_inherited_whatever_its_value(db_session, sides, stored):
    _store(db_session, sides, stored)
    assert _inherited(db_session, sides) is stored


def test_both_twins_stored_cannot_be_inherited(db_session, sides):
    _store(db_session, sides, True)
    _store(db_session, sides, False)
    assert _inherited(db_session, sides) is None


def _block(reversible):
    """A reaction block as the workflow sees it, built without any schema validation."""
    identity = SpeciesEntryIdentityPayload
    return SimpleNamespace(
        reversible=reversible,
        reactants=[SimpleNamespace(species_entry=identity(**H)) for _ in range(2)],
        products=[SimpleNamespace(species_entry=identity(**H2))],
    )


def _stored_sides(db_session):
    h = resolve_species_entry(db_session, SpeciesEntryIdentityPayload(**H))
    h2 = resolve_species_entry(db_session, SpeciesEntryIdentityPayload(**H2))
    return compress_species_stoichiometry([h, h]), compress_species_stoichiometry([h2])


def test_the_workflow_helper_states_inherits_or_refuses_on_an_unvalidated_block(db_session):
    # Stated: returned as is.
    assert reversible_or_inherited(db_session, _block(False)) is False
    assert reversible_or_inherited(db_session, _block(True)) is True
    # Unstated and nothing to inherit: refused with the coded error, never defaulted.
    with pytest.raises(CodedValueError) as refusal:
        reversible_or_inherited(db_session, _block(None))
    assert refusal.value.code == CODE_REACTION_REVERSIBLE_REQUIRED
    assert refusal.value.context == {"field": "reaction.reversible"}
    # Unstated with one stored: inherited. With both stored: refused again.
    sides = _stored_sides(db_session)
    _store(db_session, sides, False)
    assert reversible_or_inherited(db_session, _block(None)) is False
    _store(db_session, sides, True)
    with pytest.raises(CodedValueError):
        reversible_or_inherited(db_session, _block(None))


def test_the_twin_is_the_row_with_the_opposite_value_and_only_that(db_session, sides):
    reactants, products = sides
    irreversible = _store(db_session, sides, False)
    assert reversible_twin(db_session, reversible=True, reactant_stoichiometry=reactants, product_stoichiometry=products) == irreversible
    assert reversible_twin(db_session, reversible=False, reactant_stoichiometry=reactants, product_stoichiometry=products) is None


@pytest.mark.parametrize("first,second", [(False, True), (True, False)])
def test_resolving_with_a_sink_reports_the_twin_once_and_only_when_there_is_one_in_either_order(db_session, sides, first, second):
    warnings: list[UploadWarning] = []
    _store(db_session, sides, first, warnings)
    assert warnings == []
    again: list[UploadWarning] = []
    twin_maker = _store(db_session, sides, second, again)
    assert [w.code for w in again] == [W_REACTION_REVERSIBLE_TWIN]
    assert twin_maker.reversible is second
    # No sink, no warning, same resolution: the sink is an addition, not a behaviour change.
    assert _store(db_session, sides, second) == twin_maker
