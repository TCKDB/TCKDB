"""Two states of one network with the same composition are a coded 422, not a raw 500.

A state's composition is the multiset of its participants' species *entries*. Two states that
resolve to the same entries collide on ``uq_network_state_network_id`` (network, composition
hash). That used to surface as an ``IntegrityError`` (HTTP 500); it is now refused before the
insert as ``network_state_duplicate_composition`` (422), naming the two state keys.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.network import Network
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.workflows.network_pdep import persist_network_pdep_upload
from tests.workflows.test_network_pdep_upload import _full_payload

CODE = "network_state_duplicate_composition"


def _add_state(payload: dict, key: str, species_key: str) -> dict:
    """A well on ``species_key``, wired to ``exit`` so the channel graph stays connected."""
    payload["states"].append({"key": key, "kind": "well", "participants": [{"species_key": species_key}]})
    payload["channels"].append(
        {
            "key": f"{key}_path",
            "source_state_key": key,
            "sink_state_key": "exit",
            "kind": "dissociation",
            "microreaction_paths": [{"micro_reaction_key": "rxn_ho2_elim", "transition_state_key": "ts_elim"}],
        }
    )
    return payload


def _with_second_well() -> dict:
    return _add_state(_full_payload(include_solve=False), "well_RO2_again", "ethylperoxy")


def _two_flat_butene_payload() -> dict:
    """Two species keys, both ``CC=CC`` with no marks, geometry or label: one unresolved entry."""
    payload = _full_payload(include_solve=False)
    base = next(s for s in payload["species"] if s["key"] == "ethylperoxy")
    # same SMILES -> same species -> (no stereo to tell them apart) same entry
    payload["species"].append({"key": "twin", "species_entry": deepcopy(base["species_entry"])})
    return _add_state(payload, "well_twin", "twin")


def test_two_states_with_one_composition_are_a_coded_422(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        request = NetworkPDepUploadRequest(**_with_second_well())
        networks_before = session.scalar(select(func.count()).select_from(Network))
        with pytest.raises(CodedValueError) as excinfo:
            persist_network_pdep_upload(session, request)
        error = excinfo.value
        assert error.code == CODE
        text = str(error)
        assert "well_RO2" in text and "well_RO2_again" in text
        assert "id=" not in text
        assert session.scalar(select(func.count()).select_from(Network)) == networks_before


def test_two_species_keys_that_resolve_to_one_entry_collide_the_same_way(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        request = NetworkPDepUploadRequest(**_two_flat_butene_payload())
        with pytest.raises(CodedValueError) as excinfo:
            persist_network_pdep_upload(session, request)
        assert excinfo.value.code == CODE


def _state_with_two_keys_for_one_entry_payload() -> dict:
    """One state whose two participants are different species keys that resolve to one entry."""
    payload = _full_payload(include_solve=False)
    base = next(s for s in payload["species"] if s["key"] == "ethylperoxy")
    payload["species"].append({"key": "twin", "species_entry": deepcopy(base["species_entry"])})
    payload = _add_state(payload, "well_pair", "ethylperoxy")
    state = next(s for s in payload["states"] if s["key"] == "well_pair")
    state["participants"].append({"species_key": "twin"})
    return payload


def test_two_keys_for_one_entry_inside_a_state_are_a_coded_422_not_a_500(db_conn) -> None:
    """It used to be a raw ``UniqueViolation`` on ``pk_network_state_participant`` (HTTP 500)."""
    with Session(db_conn) as session, session.begin():
        request = NetworkPDepUploadRequest(**_state_with_two_keys_for_one_entry_payload())
        with pytest.raises(CodedValueError) as excinfo:
            persist_network_pdep_upload(session, request)
    error = excinfo.value
    assert error.code == "network_state_duplicate_participant"
    assert "well_pair" in str(error) and "id=" not in str(error)


def test_over_http_a_duplicate_participant_is_a_422(client) -> None:
    response = client.post("/api/v1/uploads/networks/pdep", json=_state_with_two_keys_for_one_entry_payload())
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "network_state_duplicate_participant"


def test_the_unmodified_network_still_uploads(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        network = persist_network_pdep_upload(
            session, NetworkPDepUploadRequest(**_full_payload(include_solve=False))
        )
        assert network.id is not None


def test_over_http_it_is_a_422_with_the_code_not_a_500(client) -> None:
    response = client.post("/api/v1/uploads/networks/pdep", json=_with_second_well())
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == CODE
    assert body["context"]["state_keys"] == ["well_RO2", "well_RO2_again"]
