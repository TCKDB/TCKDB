"""``resolve_unstated_reversible`` and ``reversible_twin``: the service half of #598, with no request schema in the way."""

from __future__ import annotations

import pytest
from tckdb_schemas.upload_warning import UploadWarning

from app.services.reaction_resolution import (
    W_REACTION_REVERSIBLE_TWIN,
    compress_species_stoichiometry,
    resolve_chem_reaction,
    resolve_unstated_reversible,
    reversible_twin,
)
from tests.services.scientific_read._factories import make_species, make_species_entry, next_inchi_key


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


def _unstated(db_session, sides):
    reactants, products = sides
    return resolve_unstated_reversible(db_session, reactant_stoichiometry=reactants, product_stoichiometry=products)


def test_nothing_stored_defaults_to_reversible(db_session, sides):
    assert _unstated(db_session, sides) == (True, "defaulted")


@pytest.mark.parametrize("stored", [True, False])
def test_exactly_one_stored_reaction_is_inherited_whatever_its_value(db_session, sides, stored):
    _store(db_session, sides, stored)
    assert _unstated(db_session, sides) == (stored, "inherited")


def test_both_twins_stored_cannot_be_inherited(db_session, sides):
    _store(db_session, sides, True)
    _store(db_session, sides, False)
    assert _unstated(db_session, sides) == (True, "defaulted")


def test_the_twin_is_the_row_with_the_opposite_value_and_only_that(db_session, sides):
    reactants, products = sides
    irreversible = _store(db_session, sides, False)
    assert reversible_twin(db_session, reversible=True, reactant_stoichiometry=reactants, product_stoichiometry=products) == irreversible
    assert reversible_twin(db_session, reversible=False, reactant_stoichiometry=reactants, product_stoichiometry=products) is None


def test_resolving_with_a_sink_reports_the_twin_once_and_only_when_there_is_one(db_session, sides):
    warnings: list[UploadWarning] = []
    _store(db_session, sides, False, warnings)
    assert warnings == []
    again: list[UploadWarning] = []
    twin_maker = _store(db_session, sides, True, again)
    assert [w.code for w in again] == [W_REACTION_REVERSIBLE_TWIN]
    assert twin_maker.reversible is True
    # No sink, no warning, same resolution: the sink is an addition, not a behaviour change.
    assert _store(db_session, sides, True) == twin_maker
