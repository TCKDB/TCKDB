"""A network channel's kind is derived from its states; a supplied kind is only ever checked.

Omitted -> derived and stored. Supplied and agreeing -> accepted. Supplied and disagreeing ->
``network_channel_kind_mismatch`` (422) naming the channel key, the supplied kind and the derived kind.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.app_user import AppUser
from app.db.models.common import NetworkChannelKind
from app.db.models.network_pdep import NetworkChannel
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.workflows.network_pdep import persist_network_pdep_upload
from tests.workflows.test_network_pdep_upload import _parallel_path_payload, _well_skipping_payload

CODE = "network_channel_kind_mismatch"


def _without_kinds(payload: dict) -> dict:
    payload = deepcopy(payload)
    for channel in payload["channels"]:
        channel.pop("kind", None)
    return payload


def _coded(caught) -> object:
    """The ``CodedValidationError`` Pydantic keeps in ``errors()[0]["ctx"]["error"]``."""
    return caught.value.errors()[0]["ctx"]["error"]


def _kinds(request: NetworkPDepUploadRequest) -> dict[str, str]:
    return {c.key: c.kind.value for c in request.channels}


def test_omitted_kind_is_derived_for_isomerization_association_and_dissociation() -> None:
    request = NetworkPDepUploadRequest(**_without_kinds(_parallel_path_payload()))
    assert _kinds(request) == {
        "association_path": "association",
        "dissociation_path": "dissociation",
        "elimination_path": "dissociation",
        "isomerization_path": "isomerization",
    }


def test_omitted_kind_is_exchange_between_two_fragment_sets_well_skipping() -> None:
    request = NetworkPDepUploadRequest(**_without_kinds(_well_skipping_payload()))
    assert _kinds(request)["chemically_activated_path"] == "exchange"


def test_termolecular_is_derived_like_bimolecular() -> None:
    payload = _without_kinds(_parallel_path_payload())
    next(s for s in payload["states"] if s["key"] == "entrance")["kind"] = "termolecular"
    assert _kinds(NetworkPDepUploadRequest(**payload))["association_path"] == "association"
    assert _kinds(NetworkPDepUploadRequest(**payload))["dissociation_path"] == "dissociation"
    skipping = _without_kinds(_well_skipping_payload())
    next(s for s in skipping["states"] if s["key"] == "exit")["kind"] = "termolecular"
    assert _kinds(NetworkPDepUploadRequest(**skipping))["chemically_activated_path"] == "exchange"


def test_agreeing_kinds_are_accepted_unchanged() -> None:
    request = NetworkPDepUploadRequest(**_parallel_path_payload())
    assert _kinds(request)["isomerization_path"] == "isomerization"


@pytest.mark.parametrize(
    ("channel_key", "supplied", "derived"),
    [
        ("association_path", "isomerization", "association"),
        ("isomerization_path", "association", "isomerization"),
        ("elimination_path", "exchange", "dissociation"),
        ("association_path", "stabilization", None),
    ],
)
def test_disagreeing_kind_is_a_coded_refusal_with_no_ids(channel_key, supplied, derived) -> None:
    payload = deepcopy(_parallel_path_payload())
    next(c for c in payload["channels"] if c["key"] == channel_key)["kind"] = supplied
    if derived is None:  # a value that no longer exists is an unknown enum member, not a mismatch
        with pytest.raises(ValueError, match="kind"):
            NetworkPDepUploadRequest(**payload)
        return
    with pytest.raises(ValidationError) as caught:
        NetworkPDepUploadRequest(**payload)
    error = _coded(caught)
    assert error.code == CODE
    assert error.context["channel_key"] == channel_key
    assert error.context["supplied_kind"] == supplied
    assert error.context["derived_kind"] == derived
    text = str(error)
    assert channel_key in text and supplied in text and derived in text
    assert "id=" not in text and "_id" not in text


def test_a_well_skipping_channel_with_the_wrong_kind_is_refused_the_same_way() -> None:
    payload = deepcopy(_well_skipping_payload())
    payload["channels"][-1]["kind"] = "association"
    with pytest.raises(ValidationError) as caught:
        NetworkPDepUploadRequest(**payload)
    assert _coded(caught).code == CODE


def test_omitted_kind_is_stored_derived(db_conn) -> None:
    request = NetworkPDepUploadRequest(**_without_kinds(_parallel_path_payload()))
    with Session(db_conn) as session, session.begin():
        actor = AppUser(username="channel_kind_omitted")
        session.add(actor)
        session.flush()
        network = persist_network_pdep_upload(session, request, created_by=actor.id)
        stored = {
            row.channel_key: row.kind
            for row in session.scalars(select(NetworkChannel).where(NetworkChannel.network_id == network.id))
        }
        assert stored["association_path"] is NetworkChannelKind.association
        assert stored["dissociation_path"] is NetworkChannelKind.dissociation
        assert stored["isomerization_path"] is NetworkChannelKind.isomerization
        session.rollback()


def test_exchange_and_termolecular_are_stored_derived(db_conn) -> None:
    payload = _without_kinds(_well_skipping_payload())
    next(s for s in payload["states"] if s["key"] == "exit")["kind"] = "termolecular"
    with Session(db_conn) as session, session.begin():
        actor = AppUser(username="channel_kind_exchange")
        session.add(actor)
        session.flush()
        network = persist_network_pdep_upload(session, NetworkPDepUploadRequest(**payload), created_by=actor.id)
        row = session.scalars(
            select(NetworkChannel).where(
                NetworkChannel.network_id == network.id, NetworkChannel.channel_key == "chemically_activated_path"
            )
        ).one()
        assert row.kind is NetworkChannelKind.exchange
        session.rollback()


def test_a_request_built_without_validation_meets_the_same_refusal_in_the_workflow(db_conn) -> None:
    request = NetworkPDepUploadRequest(**_parallel_path_payload())
    request.channels[0].kind = NetworkChannelKind.exchange  # assignment skips the validator
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as caught:
            persist_network_pdep_upload(session, request)
        assert caught.value.code == CODE
        assert request.channels[0].key in str(caught.value) and "id=" not in str(caught.value)
        session.rollback()


def test_over_http_the_mismatch_is_a_422_with_the_code(client) -> None:
    payload = deepcopy(_parallel_path_payload())
    next(c for c in payload["channels"] if c["key"] == "association_path")["kind"] = "isomerization"
    response = client.post("/api/v1/uploads/networks/pdep", json=payload)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == CODE
    assert body["context"]["channel_key"] == "association_path"
    assert body["context"]["derived_kind"] == "association"


def test_a_plain_string_kind_that_disagrees_is_a_coded_422_not_a_500(db_conn) -> None:
    request = NetworkPDepUploadRequest(**_parallel_path_payload())
    channels = [c.model_copy(update={"kind": "exchange"}) for c in request.channels[:1]] + request.channels[1:]
    request = NetworkPDepUploadRequest.model_construct(**{**dict(request), "channels": channels})
    assert channels[0].kind == "exchange" and not isinstance(channels[0].kind, NetworkChannelKind)
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as caught:
            persist_network_pdep_upload(session, request)
        assert caught.value.code == CODE
        assert "exchange" in str(caught.value) and "id=" not in str(caught.value)
        session.rollback()
