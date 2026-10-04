"""#598: ``reaction.reversible`` is inherited or refused, never guessed, and a twin is reported on every route.

``reversible`` is part of a graph reaction's identity, so one set of participants stored as reversible and
as irreversible is two ``chem_reaction`` rows. That identity is unchanged here. What a deposit that does not
state it gets is: the value of the one stored reaction (or of the reaction its transition state or
determination anchors it to), and otherwise a coded refusal, ``reaction_reversible_required``. There is no
default and no nullable column. A deposit that creates or hits a twin is told, on every route that can.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

import app.workers.upload_worker as upload_worker
from app.db.models.kinetics import Kinetics, KineticsDetermination
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.services.reaction_resolution import W_REACTION_REVERSIBLE_TWIN
from tests.api.test_api_kinetics_declarations import (
    BUNDLE,
    KINETICS,
    REACTION,
    _bundle,
    _code,
    _determination,
    _standalone,
)
from tests.workers.test_upload_worker_warnings import _direct, _enqueue

REACTION_ROUTE = "/api/v1/uploads/reactions"
NETWORK_ROUTE = "/api/v1/uploads/networks"
PDEP_ROUTE = "/api/v1/uploads/networks/pdep"
REQUIRED = "reaction_reversible_required"


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


def _reaction_body(reversible: bool, reaction: dict | None = None) -> dict:
    reaction = reaction or REACTION
    return {"reversible": reversible, "reactants": reaction["reactants"], "products": reaction["products"]}


# -- the kinetics route: inherit from each source, else refuse ------------------------------------


def test_a_bare_first_deposit_is_refused_with_a_coded_error_and_writes_nothing(client, db_session):
    response = client.post(KINETICS, json=_unstated())
    assert _code(response) == REQUIRED
    assert response.json()["context"]["field"] == "reaction.reversible"
    assert "State reversible" in response.json()["detail"]
    assert _reactions(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(Kinetics)) == 0
    # The same deposit with the value stated is accepted: the refusal is about the missing fact alone.
    assert client.post(KINETICS, json=_with(True)).status_code == 201


def test_null_is_the_same_as_omitted_and_both_are_refused_when_nothing_can_be_inherited(client):
    body = _standalone()
    body["reaction"] = {**body["reaction"], "reversible": None}
    assert _code(client.post(KINETICS, json=body)) == REQUIRED


@pytest.mark.parametrize("stored", [True, False])
def test_an_unstated_value_inherits_the_one_stored_reaction_whatever_its_value(client, db_session, stored):
    assert client.post(KINETICS, json=_with(stored)).status_code == 201
    response = client.post(KINETICS, json=_unstated())
    assert response.status_code == 201, response.text[:800]
    assert _reactions(db_session) == [stored], "omission must not create a twin"
    assert W_REACTION_REVERSIBLE_TWIN not in _codes(response)
    entries = db_session.scalars(select(ReactionEntry)).all()
    assert len(entries) == 2 and len({e.reaction_id for e in entries}) == 1


def test_when_both_twins_are_stored_there_is_nothing_to_inherit_and_the_deposit_is_refused(client, db_session):
    assert client.post(KINETICS, json=_with(False)).status_code == 201
    assert client.post(KINETICS, json=_with(True)).status_code == 201
    assert _code(client.post(KINETICS, json=_unstated())) == REQUIRED
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


def test_a_transition_state_anchored_rate_inherits_the_anchored_reaction_even_when_nothing_else_is_stored(
    client, db_session, anchored
):
    entry, ts = anchored
    response = client.post(KINETICS, json=_anchor(ts))
    assert response.status_code == 201, response.text[:800]
    assert db_session.scalars(select(Kinetics)).one().reaction_entry_id == entry.id
    assert _reactions(db_session) == [False]


def test_an_anchored_rate_that_states_the_opposite_is_still_refused(client, db_session, anchored):
    entry, ts = anchored
    body = _anchor(ts)
    body["reaction"] = {**body["reaction"], "reversible": True}
    assert client.post(KINETICS, json=body).status_code == 422


def test_a_rate_citing_a_determination_inherits_its_reaction_with_nothing_else_to_go_on(client, db_session):
    first = _with(False, determination=_determination("set-A"))
    assert client.post(KINETICS, json=first).status_code == 201, "the first deposit states it"
    det = db_session.scalars(select(KineticsDetermination)).one()
    # Make the stored reaction ambiguous so that only the determination can supply the value.
    assert client.post(KINETICS, json=_with(True)).status_code == 201
    cite = _unstated(determination={"determination_ref": det.public_ref, "representation_role": "complete"})
    response = client.post(KINETICS, json=cite)
    assert response.status_code == 201, response.text[:800]
    joined = db_session.scalars(select(Kinetics).order_by(Kinetics.id.desc()).limit(1)).one()
    assert joined.determination_id == det.id and joined.reaction_entry_id == det.reaction_entry_id
    assert _reactions(db_session) == [False, True]


def test_the_stated_value_still_has_to_be_a_boolean(client):
    body = _standalone()
    body["reaction"] = {**body["reaction"], "reversible": "maybe"}
    assert client.post(KINETICS, json=body).status_code == 422


# -- twins: reported on every route that can create one, in both orders -------------------------


@pytest.mark.parametrize("first,second", [(False, True), (True, False)], ids=["irreversible-then-reversible", "reversible-then-irreversible"])
def test_a_stated_value_that_makes_a_twin_is_accepted_and_the_twin_is_named_in_either_order(client, db_session, first, second):
    one = client.post(KINETICS, json=_with(first))
    assert one.status_code == 201 and W_REACTION_REVERSIBLE_TWIN not in _codes(one)
    two = client.post(KINETICS, json=_with(second))
    assert two.status_code == 201, two.text[:800]
    assert _reactions(db_session) == [first, second], "identity is unchanged: two rows"
    warning = next(w for w in two.json()["warnings"] if w["code"] == W_REACTION_REVERSIBLE_TWIN)
    other = db_session.scalars(select(ChemReaction).where(ChemReaction.reversible.is_(first))).one()
    assert other.public_ref in warning["message"]
    assert str(other.id) not in warning["message"].replace(other.public_ref, "")
    assert warning["field"] == "reaction.reversible"


def test_restating_the_same_value_is_not_a_twin(client, db_session):
    for _ in range(2):
        response = client.post(KINETICS, json=_with(False))
        assert response.status_code == 201
        assert W_REACTION_REVERSIBLE_TWIN not in _codes(response)
    assert _reactions(db_session) == [False]


@pytest.mark.parametrize("first,second", [(False, True), (True, False)])
def test_the_reaction_route_names_a_twin_in_either_order(client, db_session, first, second):
    one = client.post(REACTION_ROUTE, json=_reaction_body(first))
    assert one.status_code == 201, one.text[:800]
    assert W_REACTION_REVERSIBLE_TWIN not in _codes(one)
    assert W_REACTION_REVERSIBLE_TWIN in _codes(client.post(REACTION_ROUTE, json=_reaction_body(second)))


def test_the_computed_reaction_route_names_a_twin(client, db_session):
    assert client.post(KINETICS, json=_with(False)).status_code == 201
    response = client.post(BUNDLE, json=_bundle())  # a bundle states reversible: true
    assert response.status_code == 201, response.text[:800]
    assert W_REACTION_REVERSIBLE_TWIN in _codes(response)
    assert _reactions(db_session) == [False, True]


def _network(reversible: bool | None = True, **extra) -> dict:
    reaction = {k: v for k, v in _reaction_body(True).items() if k != "reversible"}
    if reversible is not None:
        reaction["reversible"] = reversible
    return {"name": "reversible network", "reactions": [{"reaction": reaction}], **extra}


def test_the_network_route_names_a_twin_inherits_and_refuses_like_the_kinetics_route(client, db_session):
    # Refused when nothing can be inherited, with the same coded error (not raw pydantic text).
    refused = client.post(NETWORK_ROUTE, json=_network(None))
    assert _code(refused) == REQUIRED and refused.json()["code"] != "validation_error"
    assert refused.json()["context"]["field"] == "reactions[0].reaction.reversible"
    assert _reactions(db_session) == []
    # A stated value stores; the opposite value on a later network is a twin.
    assert client.post(NETWORK_ROUTE, json=_network(False)).status_code == 201
    twin = client.post(NETWORK_ROUTE, json=_network(True))
    assert twin.status_code == 201 and W_REACTION_REVERSIBLE_TWIN in _codes(twin)
    assert _reactions(db_session) == [False, True]
    # Both stored: nothing to inherit.
    assert _code(client.post(NETWORK_ROUTE, json=_network(None))) == REQUIRED


def test_the_network_refusal_names_the_reaction_that_did_not_state_it(client):
    body = {
        "name": "second reaction omits it",
        "reactions": [
            {"reaction": _reaction_body(True)},
            {
                "reaction": {
                    "reactants": [{"species_entry": {"smiles": "[Ar]", "charge": 0, "multiplicity": 1}}],
                    "products": [{"species_entry": {"smiles": "[Kr]", "charge": 0, "multiplicity": 1}}],
                }
            },
        ],
    }
    refused = client.post(NETWORK_ROUTE, json=body)
    assert _code(refused) == REQUIRED
    assert refused.json()["context"]["field"] == "reactions[1].reaction.reversible"


@pytest.mark.parametrize("stored", [True, False])
def test_a_network_reaction_that_omits_reversible_inherits_the_one_stored_reaction(client, db_session, stored):
    assert client.post(REACTION_ROUTE, json=_reaction_body(stored)).status_code == 201
    response = client.post(NETWORK_ROUTE, json=_network(None))
    assert response.status_code == 201, response.text[:800]
    assert _reactions(db_session) == [stored]
    assert W_REACTION_REVERSIBLE_TWIN not in _codes(response)


def _pdep_with_reversible_twin() -> dict:
    from tests.api.test_api_network_reads import _pdep_payload

    return _pdep_payload()


PDEP_REACTION = {
    "reactants": [
        {"species_entry": {"smiles": "C[CH2]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[O][O]", "charge": 0, "multiplicity": 3}},
    ],
    "products": [{"species_entry": {"smiles": "CCO[O]", "charge": 0, "multiplicity": 2}}],
}


def test_the_pdep_route_names_a_twin_for_its_micro_reactions(client, db_session):
    assert client.post(REACTION_ROUTE, json={"reversible": False, **PDEP_REACTION}).status_code == 201
    response = client.post(PDEP_ROUTE, json=_pdep_with_reversible_twin())  # its micro reaction is reversible: true
    assert response.status_code == 201, response.text[:800]
    assert W_REACTION_REVERSIBLE_TWIN in _codes(response)
    assert _reactions(db_session) == [False, True]


# -- the job route reports exactly what the direct route does (#647) --------------------------------


def _same_through_a_job(client, db_session, direct_url, job_url, payload):
    expected = _direct(client, direct_url, payload)
    assert W_REACTION_REVERSIBLE_TWIN in [w["code"] for w in expected], expected
    job = _enqueue(client, job_url, payload)
    upload_worker.run_one_job(client._db_session, job)
    read = client.get(f"/api/v1/jobs/{job.id}")
    assert read.status_code == 200, read.text[:600]
    result = read.json()["result"]
    assert "result_unavailable" not in result
    assert result["warnings"] == expected


def test_the_reaction_job_reports_the_twin_the_direct_route_reports(client, db_session):
    assert client.post(REACTION_ROUTE, json=_reaction_body(False)).status_code == 201
    _same_through_a_job(client, db_session, REACTION_ROUTE, "/api/v1/jobs/reaction", _reaction_body(True))


def test_the_network_job_reports_the_twin_the_direct_route_reports(client, db_session):
    assert client.post(REACTION_ROUTE, json=_reaction_body(False)).status_code == 201
    _same_through_a_job(client, db_session, NETWORK_ROUTE, "/api/v1/jobs/network", _network(True))


def test_the_pdep_job_reports_the_twin_the_direct_route_reports(client, db_session):
    assert client.post(REACTION_ROUTE, json={"reversible": False, **PDEP_REACTION}).status_code == 201
    _same_through_a_job(client, db_session, PDEP_ROUTE, "/api/v1/jobs/network/pdep", _pdep_with_reversible_twin())


def test_a_job_that_cannot_inherit_fails_with_the_same_coded_reason(client, db_session):
    from app.api.error_contract import CodedValueError

    job = _enqueue(client, "/api/v1/jobs/network", _network(None))
    with pytest.raises(CodedValueError) as refusal:
        upload_worker.run_one_job(client._db_session, job)
    assert refusal.value.code == REQUIRED
