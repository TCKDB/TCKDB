"""Tests for ``tckdb_mcp.tools.network_kinetics_selection`` (``tckdb_select_network_kinetics``).

Request construction and verbatim pass-through only; agreement with the live server and with the Python client is
covered by ``backend/tests/api/scientific/test_network_select_parity.py``.
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

TOOL_NAME = "tckdb_select_network_kinetics"
REF = "net_01HZ5K9X2A"
QUESTION: dict[str, Any] = {
    "coefficient_basis": "kernel",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure_min_bar": 0.5,
    "pressure_max_bar": 5.0,
    "bath": {"components": [{"species_ref": "spe_abc"}]},
    "partition": {"retained": ["a" * 64]},
    "channel_key": "assoc",
    "observable": "product_resolved_coefficient",
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
    return {"network_ref": REF, **QUESTION, **changes}


def test_tool_is_registered_with_a_closed_schema_and_no_paging_or_id_argument():
    (entry,) = [t for t in list_tools_payload() if t["name"] == TOOL_NAME]
    schema = entry["inputSchema"]
    assert schema["required"] == [
        "network_ref", "coefficient_basis", "temperature_min_k", "temperature_max_k", "pressure_min_bar",
        "pressure_max_bar", "bath", "partition",
    ]
    assert schema["additionalProperties"] is False
    assert not {"max_candidates", "bounds", "limit", "offset", "page", "cursor"} & set(schema["properties"])
    assert not [k for k in schema["properties"] if k.endswith("_id")]


def test_posts_to_the_select_path_with_only_what_was_supplied():
    seen, handler = _recording()
    _call(_client(handler), _args())
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path.endswith(f"/scientific/networks/{REF}/kinetics/select")
    assert "assoc" not in request.url.path  # channel_key is a body field
    assert json.loads(request.content) == QUESTION
    assert "profile" not in request.url.params


def test_forwards_every_optional_field_and_the_profile():
    seen, handler = _recording()
    extra = {
        "scope": "projected_bundle",
        "outputs": [{"channel_key": "elim", "observable": "product_resolved_coefficient"}],
        "degeneracy_applied": True,
        "boundaries": [{"state": "c" * 64, "kind": "absorbing"}],
        "regime": {"kind": "time_independent"},
        "source_composition_hash": "a" * 64,
        "sink_composition_hash": "b" * 64,
        "objective": "model_fidelity",
        "reference_model_ref": "nsolve_xyz",
        "policy": "latest",
        "mode": "first",
        "min_review_status": "approved",
        "phase": "gas",
        "quantity": "rate_coefficient",
    }
    _call(_client(handler), _args(**extra, profile="curated"))
    assert json.loads(seen[0].content) == {**QUESTION, **extra}
    assert seen[0].url.params["profile"] == "curated"


def test_the_servers_answer_is_returned_verbatim():
    answer = {"outcome": "incomparable_alternatives", "basis": "b", "selection": None}
    _, handler = _recording(answer)
    assert _call(_client(handler), _args()) == answer


def test_a_server_refusal_keeps_its_own_code():
    refusal = {"code": "network_selection_population_too_large", "detail": "d"}
    _, handler = _recording(refusal, status=422)
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), _args())
    assert "network_selection_population_too_large" in json.dumps(caught.value.to_payload())


def test_a_question_that_is_not_well_posed_is_left_for_the_server_to_refuse():
    seen, handler = _recording({"code": "validation_error"}, status=422)
    with pytest.raises(MCPToolError):
        _call(_client(handler), _args(outputs=[{"channel_key": "x", "observable": "y"}]))  # single channel + outputs
    assert len(seen) == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {k: v for k, v in _args().items() if k != "network_ref"},
        _args(network_ref=12),
        _args(network_ref="12"),
        _args(network_ref="rxe_abc"),
        _args(network_ref=""),
        _args(network_ref="net_a/b"),
        {k: v for k, v in _args().items() if k != "coefficient_basis"},
        _args(coefficient_basis="elementary_coefficient"),
        {k: v for k, v in _args().items() if k != "bath"},
        _args(bath={"components": []}),
        _args(bath={"components": [{"species_ref": "spc_x"}]}),
        _args(bath={"components": [{"species_id": 3}]}),
        _args(bath={"components": [{"species_ref": "spe_x", "mole_fraction": "half"}]}),
        _args(bath={"species_refs": ["spe_x"]}),
        {k: v for k, v in _args().items() if k != "partition"},
        _args(partition={"retained": "a"}),
        _args(partition={"lumps": ["ab"]}),
        _args(partition={"retained": [], "surprise": 1}),
        _args(temperature_min_k="500"),
        _args(pressure_max_bar=True),
        _args(scope="whole_network"),
        _args(objective="best"),
        _args(reference_model_ref="net_x"),
        _args(outputs="assoc"),
        _args(outputs=[{"channel_key": "a"}]),
        _args(outputs=[{"channel_key": "a", "observable": "o", "surprise": 1}]),
        _args(boundaries=[{"state": "a"}]),
        _args(regime={"kind": "sometimes"}),
        _args(degeneracy_applied="yes"),
        _args(network_id=3),
        _args(channel_id=3),
        _args(solve_id=3),
        _args(max_candidates=5),
        _args(bounds={"solves": 1}),
        _args(limit=10),
        _args(offset=0),
        _args(policy="best"),
        _args(mode="one"),
        _args(profile="official"),
        _args(surprise=1),
        _args(quantity="net_rate"),
    ],
)
def test_invalid_input_is_refused_before_any_request(arguments):
    seen, handler = _recording()
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), arguments)
    assert caught.value.to_payload()["code"] == "invalid_input"
    assert seen == []


def test_a_paging_argument_is_refused_with_an_explanation_of_the_fixed_bounds():
    _, handler = _recording()
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), _args(limit=10))
    assert "network_selection_population_too_large" in json.dumps(caught.value.to_payload())


def test_arguments_sent_as_null_are_omitted_not_forwarded():
    seen, handler = _recording()
    _call(_client(handler), _args(scope=None, outputs=None, policy=None, mode=None, objective=None, profile=None))
    assert json.loads(seen[0].content) == QUESTION
    assert None not in json.loads(seen[0].content).values()


def test_a_null_required_argument_is_still_a_missing_one():
    seen, handler = _recording()
    with pytest.raises(MCPToolError):
        _call(_client(handler), _args(partition=None))
    assert seen == []


def test_the_path_ref_is_url_quoted_and_a_reference_ref_is_validated():
    seen, handler = _recording()
    _call(_client(handler), _args(network_ref="net_ok-1_2"))
    assert seen[0].url.path.endswith("/networks/net_ok-1_2/kinetics/select")
