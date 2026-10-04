"""Tests for ``tckdb_mcp.tools.reaction_kinetics_selection`` (``tckdb_select_reaction_entry_kinetics``).

Request construction and verbatim pass-through only; agreement with the live server and with the Python client is
covered by ``backend/tests/api/scientific/test_kinetics_select_parity.py``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool, list_tools_payload

TOOL_NAME = "tckdb_select_reaction_entry_kinetics"
REF = "rxe_01HZ5K9X2A"
QUESTION: dict[str, Any] = {
    "direction": "forward",
    "target": {"kind": "whole_reaction"},
    "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure": {"kind": "independent"},
}


def _client(handler) -> TCKDBHttpClient:
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1", api_key=None, timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )


def _recording(answer: dict[str, Any] | None = None, status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer if answer is not None else {"outcome": "policy_preferred"})

    return seen, handler


def _call(client, arguments: dict[str, Any]):
    return dispatch_tool(TOOL_NAME, arguments, client, Config.from_env(env={}))


def _args(**changes: Any) -> dict[str, Any]:
    return {"reaction_entry_ref": REF, **QUESTION, **changes}


def test_tool_is_registered_with_a_closed_schema_and_no_paging_argument():
    (entry,) = [t for t in list_tools_payload() if t["name"] == TOOL_NAME]
    schema = entry["inputSchema"]
    assert schema["required"] == [
        "reaction_entry_ref", "direction", "target", "coefficient_basis", "temperature_min_k",
        "temperature_max_k", "pressure",
    ]
    assert schema["additionalProperties"] is False
    assert not {"max_candidates", "limit", "offset", "page", "cursor"} & set(schema["properties"])
    assert not [k for k in schema["properties"] if k.endswith("_id")]


def test_posts_to_the_select_path_with_only_what_was_supplied():
    seen, handler = _recording()
    _call(_client(handler), _args())
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == f"/api/v1/scientific/reaction-entries/{REF}/kinetics/select"
    assert json.loads(request.content) == QUESTION
    assert dict(request.url.params) == {}


def test_forwards_every_optional_field_and_the_profile():
    seen, handler = _recording()
    finite = {"kind": "finite", "min_bar": 1.0, "max_bar": 10.0}
    collider = {"components": [{"species_ref": "spc_n2", "mole_fraction": 0.79}, {"species_ref": "spc_ar", "mole_fraction": 0.21}]}
    target = {"kind": "resolved_channel", "network_ref": "net_abc", "channel_key": "A->B"}
    _call(_client(handler), _args(
        pressure=finite, collider=collider, target=target, policy="latest", mode="first",
        min_review_status="approved", phase="gas", profile="curated",
    ))
    assert json.loads(seen[0].content) == {
        **QUESTION, "pressure": finite, "collider": collider, "target": target, "policy": "latest", "mode": "first",
        "min_review_status": "approved", "phase": "gas",
    }
    assert dict(seen[0].url.params) == {"profile": "curated"}


def test_the_servers_answer_is_returned_verbatim():
    answer = {
        "outcome": "incomparable_alternatives", "basis": "nothing ranks these", "selection": None,
        "fronts": [["kdet_a", "kdet_b"]], "future_field": {"kept": True},
    }
    _, handler = _recording(answer)
    assert _call(_client(handler), _args()) == answer


def test_a_server_refusal_keeps_its_own_code():
    refusal = {"code": "kinetics_selection_population_too_large", "detail": "d", "context": {"limit": 500}}
    _, handler = _recording(refusal, status=422)
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), _args())
    assert caught.value.to_payload()["code"] == "kinetics_selection_population_too_large"


def test_a_question_that_is_not_well_posed_is_left_for_the_server_to_refuse():
    seen, handler = _recording({"code": "request_validation_failed", "detail": "d", "context": {}}, status=422)
    with pytest.raises(MCPToolError):
        _call(_client(handler), _args(pressure={"kind": "finite", "min_bar": 1.0, "max_bar": 1.0}))  # no collider
    assert len(seen) == 1  # the request was made; the refusal is the server's


@pytest.mark.parametrize(
    "arguments",
    [
        _args(reaction_entry_ref=12),
        _args(reaction_entry_ref="kin_abc"),
        _args(reaction_entry_ref="rxe_"),
        {k: v for k, v in _args().items() if k != "direction"},
        {k: v for k, v in _args().items() if k != "target"},
        {k: v for k, v in _args().items() if k != "coefficient_basis"},
        {k: v for k, v in _args().items() if k != "temperature_min_k"},
        {k: v for k, v in _args().items() if k != "temperature_max_k"},
        {k: v for k, v in _args().items() if k != "pressure"},
        _args(direction="net"),
        _args(coefficient_basis="rate_of_progress"),
        _args(temperature_min_k="500"),
        _args(temperature_max_k=True),
        _args(target="whole_reaction"),
        _args(target={"kind": "channel"}),
        _args(target={"kind": "resolved_channel", "transition_state_entry_ref": "net_x"}),
        _args(target={"kind": "resolved_channel", "transition_state_entry_id": 4}),
        _args(target={"kind": "resolved_channel", "network_ref": "tse_x", "channel_key": "c"}),
        _args(pressure="independent"),
        _args(pressure={"kind": "atmospheric"}),
        _args(pressure={"kind": "finite", "min_bar": "1"}),
        _args(pressure={"kind": "independent", "surprise": 1}),
        _args(collider={"components": []}),
        _args(collider={"components": [{"species_ref": "spe_x"}]}),
        _args(collider={"components": [{"species_id": 3}]}),
        _args(collider={"components": [{"species_ref": "spc_x", "mole_fraction": "half"}]}),
        _args(collider={"species_refs": ["spc_x"]}),
        _args(reaction_entry_id=3),
        _args(kinetics_id=3),
        _args(max_candidates=5),
        _args(limit=10),
        _args(offset=0),
        _args(policy="best"),
        _args(mode="one"),
        _args(profile="official"),
        _args(surprise=1),
    ],
)
def test_invalid_input_is_refused_before_any_request(arguments):
    seen, handler = _recording()
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), arguments)
    assert caught.value.to_payload()["code"] == "invalid_input"
    assert seen == []


def test_a_paging_argument_is_refused_with_an_explanation_of_the_fixed_cap():
    seen, handler = _recording()
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), _args(max_candidates=5))
    assert "fixed by the server" in str(caught.value.to_payload()["detail"])
    assert seen == []


def test_the_tool_never_pages_or_truncates_a_large_population():
    answer = {"outcome": "incomparable_alternatives", "candidates": [{"kinetics_ref": f"kin_{i}"} for i in range(500)]}
    seen, handler = _recording(answer)
    out = _call(_client(handler), _args())
    assert len(out["candidates"]) == 500 and len(seen) == 1


def test_arguments_sent_as_null_are_omitted_not_forwarded():
    seen, handler = _recording()
    _call(_client(handler), _args(
        mode=None, policy=None, collider=None, min_review_status=None, phase=None, profile=None, quantity=None,
    ))
    assert json.loads(seen[0].content) == QUESTION
    assert dict(seen[0].url.params) == {}


def test_a_null_required_argument_is_still_a_missing_one():
    seen, handler = _recording()
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), _args(direction=None))
    assert caught.value.to_payload()["code"] == "invalid_input" and seen == []


def test_quantity_is_accepted_and_forwarded_when_it_is_the_one_quantity():
    seen, handler = _recording()
    _call(_client(handler), _args(quantity="rate_coefficient"))
    assert json.loads(seen[0].content) == {**QUESTION, "quantity": "rate_coefficient"}
    (entry,) = [t for t in list_tools_payload() if t["name"] == TOOL_NAME]
    assert entry["inputSchema"]["properties"]["quantity"]["enum"] == ["rate_coefficient"]


def test_another_quantity_is_refused_before_any_request():
    seen, handler = _recording()
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), _args(quantity="rate_of_progress"))
    assert caught.value.to_payload()["code"] == "invalid_input" and seen == []
