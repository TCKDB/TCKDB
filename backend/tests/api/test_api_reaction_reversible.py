"""#598: an unstated ``reversible`` is not a reason to refuse or to mint a reaction, and twins are reported.

``reversible`` is part of a graph reaction's identity, so one set of participants stored as reversible and
as irreversible is two ``chem_reaction`` rows. That identity is unchanged here. What changed is what a
deposit is told, and that the kinetics route no longer refuses a rate for not stating a fact it does not need.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.reaction import ChemReaction, ReactionEntry
from app.services.reaction_resolution import W_REACTION_REVERSIBLE_DEFAULTED, W_REACTION_REVERSIBLE_TWIN
from tests.api.test_api_kinetics_declarations import BUNDLE, KINETICS, REACTION, _bundle, _standalone

REACTION_ROUTE = "/api/v1/uploads/reactions"


def _unstated(**extra) -> dict:
    body = _standalone(**extra)
    body["reaction"] = {k: v for k, v in body["reaction"].items() if k != "reversible"}
    return body


def _with(reversible: bool, **extra) -> dict:
    body = _standalone(**extra)
    body["reaction"] = {**body["reaction"], "reversible": reversible}
    return body


def _codes(response) -> list[str]:
    return [w["code"] for w in response.json().get("warnings", [])]


def _reactions(db_session) -> list[bool]:
    return list(db_session.scalars(select(ChemReaction.reversible).order_by(ChemReaction.id)))


def test_a_rate_that_does_not_state_reversible_is_accepted_and_the_default_is_disclosed(client, db_session):
    response = client.post(KINETICS, json=_unstated())
    assert response.status_code == 201, response.text[:800]
    assert _reactions(db_session) == [True]
    assert W_REACTION_REVERSIBLE_DEFAULTED in _codes(response)
    warning = next(w for w in response.json()["warnings"] if w["code"] == W_REACTION_REVERSIBLE_DEFAULTED)
    assert warning["field"] == "reaction.reversible"
    assert "microscopic reversibility" in warning["message"]


def test_an_unstated_value_joins_the_one_stored_reaction_instead_of_minting_another(client, db_session):
    assert client.post(KINETICS, json=_with(False)).status_code == 201
    response = client.post(KINETICS, json=_unstated())
    assert response.status_code == 201, response.text[:800]
    assert _reactions(db_session) == [False], "omission must not create a twin"
    assert W_REACTION_REVERSIBLE_DEFAULTED not in _codes(response)
    assert W_REACTION_REVERSIBLE_TWIN not in _codes(response)
    entries = db_session.scalars(select(ReactionEntry)).all()
    assert len(entries) == 2 and len({e.reaction_id for e in entries}) == 1


def test_when_both_twins_exist_an_unstated_value_takes_the_default_and_says_so(client, db_session):
    assert client.post(KINETICS, json=_with(False)).status_code == 201
    assert client.post(KINETICS, json=_with(True)).status_code == 201
    response = client.post(KINETICS, json=_unstated())
    assert response.status_code == 201, response.text[:800]
    assert _reactions(db_session) == [False, True]
    entry = db_session.scalars(select(ReactionEntry).order_by(ReactionEntry.id.desc()).limit(1)).one()
    assert db_session.get(ChemReaction, entry.reaction_id).reversible is True
    assert W_REACTION_REVERSIBLE_DEFAULTED in _codes(response)
    assert W_REACTION_REVERSIBLE_TWIN in _codes(response)


def test_a_stated_value_that_makes_a_twin_is_accepted_and_the_twin_is_named(client, db_session):
    first = client.post(KINETICS, json=_with(False))
    assert first.status_code == 201 and W_REACTION_REVERSIBLE_TWIN not in _codes(first)
    second = client.post(KINETICS, json=_with(True))
    assert second.status_code == 201, second.text[:800]
    assert _reactions(db_session) == [False, True], "identity is unchanged: two rows"
    warning = next(w for w in second.json()["warnings"] if w["code"] == W_REACTION_REVERSIBLE_TWIN)
    other = db_session.scalars(select(ChemReaction).where(ChemReaction.reversible.is_(False))).one()
    assert other.public_ref in warning["message"]
    assert str(other.id) not in warning["message"].replace(other.public_ref, "")
    assert warning["field"] == "reaction.reversible"


def test_restating_the_same_value_is_not_a_twin(client, db_session):
    for _ in range(2):
        response = client.post(KINETICS, json=_with(False))
        assert response.status_code == 201
        assert W_REACTION_REVERSIBLE_TWIN not in _codes(response)
    assert _reactions(db_session) == [False]


def test_the_reaction_route_names_a_twin_too(client, db_session):
    body = {"reversible": False, "reactants": _standalone()["reaction"]["reactants"], "products": _standalone()["reaction"]["products"]}
    first = client.post(REACTION_ROUTE, json=body)
    assert first.status_code == 201, first.text[:800]
    assert W_REACTION_REVERSIBLE_TWIN not in _codes(first)
    second = client.post(REACTION_ROUTE, json={**body, "reversible": True})
    assert second.status_code == 201
    assert W_REACTION_REVERSIBLE_TWIN in _codes(second)


def test_the_computed_reaction_route_names_a_twin_too(client, db_session):
    assert client.post(KINETICS, json=_with(False)).status_code == 201
    response = client.post(BUNDLE, json=_bundle())  # a bundle defaults to reversible
    assert response.status_code == 201, response.text[:800]
    assert W_REACTION_REVERSIBLE_TWIN in _codes(response)
    assert _reactions(db_session) == [False, True]


@pytest.fixture
def anchored(db_session):
    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_reaction_entry,
        make_species,
        make_species_entry,
        make_transition_state,
        make_transition_state_entry,
        next_inchi_key,
    )

    h = make_species_entry(db_session, make_species(db_session, smiles="[H]", multiplicity=2, inchi_key=next_inchi_key("RVH")))
    h2 = make_species_entry(db_session, make_species(db_session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("RVI")))
    reaction = make_chem_reaction(db_session, reactants=[h.species, h.species], products=[h2.species], reversible=False)
    entry = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[h, h], product_entries=[h2])
    ts = make_transition_state_entry(db_session, transition_state=make_transition_state(db_session, reaction_entry=entry), multiplicity=2)
    return entry, ts


def _anchor(ts, **overrides) -> dict:
    body = _unstated(
        tunneling_application={"model": "wigner", "imaginary_frequency_cm1": -1500.0, "transition_state_entry_ref": ts.public_ref},
        tunneling_model="wigner",
    )
    body.update(overrides)
    return body


def test_an_anchored_rate_without_reversible_is_accepted(client, db_session, anchored):
    entry, ts = anchored
    response = client.post(KINETICS, json=_anchor(ts))
    assert response.status_code == 201, response.text[:800]
    assert W_REACTION_REVERSIBLE_DEFAULTED not in _codes(response)


def test_an_anchored_rate_that_states_the_opposite_is_still_refused(client, db_session, anchored):
    entry, ts = anchored
    body = _anchor(ts)
    body["reaction"] = {**body["reaction"], "reversible": True}
    response = client.post(KINETICS, json=body)
    assert response.status_code == 422, response.text[:400]


def test_the_stated_value_still_has_to_be_a_boolean(client):
    body = _standalone()
    body["reaction"] = {**body["reaction"], "reversible": "maybe"}
    assert client.post(KINETICS, json=body).status_code == 422
    body["reaction"] = {**body["reaction"], "reversible": None}
    assert client.post(KINETICS, json=body).status_code == 201, "null is the same as omitted: not stated"
