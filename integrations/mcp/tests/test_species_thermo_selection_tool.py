"""Tests for ``tckdb_mcp.tools.species_thermo_selection`` (``tckdb_select_species_entry_thermo``).

Request construction and verbatim pass-through only; agreement with the live server and with the
Python client is covered by ``backend/tests/api/scientific/test_thermo_select_parity.py``.
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

TOOL_NAME = "tckdb_select_species_entry_thermo"
REF = "spe_01HZ5K9X2A"
EQUILIBRIUM = {"kind": "equilibrium_ensemble"}


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


def test_tool_is_registered_with_a_closed_schema():
    (entry,) = [t for t in list_tools_payload() if t["name"] == TOOL_NAME]
    assert entry["inputSchema"]["required"] == ["species_entry_ref", "target"]
    assert entry["inputSchema"]["additionalProperties"] is False
    assert "max_candidates" not in entry["inputSchema"]["properties"]
    assert not [k for k in entry["inputSchema"]["properties"] if k.endswith("_id")]


def test_posts_to_the_select_path_with_only_what_was_supplied():
    seen, handler = _recording()
    _call(_client(handler), {"species_entry_ref": REF, "target": EQUILIBRIUM})
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == f"/api/v1/scientific/species-entries/{REF}/thermo/select"
    assert json.loads(request.content) == {"target": EQUILIBRIUM}
    assert dict(request.url.params) == {}


def test_forwards_policy_mode_floor_conditions_and_profile():
    seen, handler = _recording()
    target = {"kind": "single_conformer", "conformer_group_ref": "cg_abc123"}
    _call(_client(handler), {
        "species_entry_ref": REF, "target": target, "policy": "latest", "result_mode": "first",
        "min_review_status": "approved", "temperature_k": 298.15, "phase": "gas", "profile": "curated",
    })
    assert json.loads(seen[0].content) == {
        "target": target, "policy": "latest", "result_mode": "first", "min_review_status": "approved",
        "temperature_k": 298.15, "phase": "gas",
    }
    assert dict(seen[0].url.params) == {"profile": "curated"}


def test_the_servers_answer_is_returned_verbatim():
    answer = {
        "outcome": "policy_conflict", "basis": "opposing rules; no automatic selection", "selection": None,
        "fronts": [["thm_a", "thm_b"]], "future_field": {"kept": True},
    }
    _, handler = _recording(answer)
    assert _call(_client(handler), {"species_entry_ref": REF, "target": EQUILIBRIUM}) == answer


def test_a_server_refusal_keeps_its_own_code():
    refusal = {"code": "thermo_selection_condition_conflict", "detail": "d", "context": {"field": "phase"}}
    _, handler = _recording(refusal, status=422)
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), {"species_entry_ref": REF, "target": EQUILIBRIUM, "phase": "aqueous"})
    assert caught.value.to_payload()["code"] == "thermo_selection_condition_conflict"


def test_a_single_conformer_without_a_group_is_left_for_the_server_to_refuse():
    seen, handler = _recording({"code": "thermo_target_group_required", "detail": "d", "context": {}}, status=422)
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), {"species_entry_ref": REF, "target": {"kind": "single_conformer"}})
    assert len(seen) == 1  # the request was made; the refusal is the server's
    assert caught.value.to_payload()["code"] == "thermo_target_group_required"


@pytest.mark.parametrize(
    "arguments",
    [
        {"species_entry_ref": 12, "target": EQUILIBRIUM},
        {"species_entry_ref": "thm_abc", "target": EQUILIBRIUM},
        {"species_entry_ref": REF},
        {"species_entry_ref": REF, "target": "equilibrium_ensemble"},
        {"species_entry_ref": REF, "target": {"kind": "ensemble"}},
        {"species_entry_ref": REF, "target": {"kind": "single_conformer", "conformer_group_ref": "thm_x"}},
        {"species_entry_ref": REF, "target": {"kind": "single_conformer", "conformer_group_id": 4}},
        {"species_entry_id": 3, "target": EQUILIBRIUM},
        {"species_entry_ref": REF, "target": EQUILIBRIUM, "conformer_group_id": 4},
        {"species_entry_ref": REF, "target": EQUILIBRIUM, "max_candidates": 5},
        {"species_entry_ref": REF, "target": EQUILIBRIUM, "policy": "best"},
        {"species_entry_ref": REF, "target": EQUILIBRIUM, "result_mode": "one"},
        {"species_entry_ref": REF, "target": EQUILIBRIUM, "profile": "official"},
        {"species_entry_ref": REF, "target": EQUILIBRIUM, "temperature_k": "298"},
        {"species_entry_ref": REF, "target": EQUILIBRIUM, "surprise": 1},
    ],
)
def test_invalid_input_is_refused_before_any_request(arguments):
    seen, handler = _recording()
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), arguments)
    assert caught.value.to_payload()["code"] == "invalid_input"
    assert seen == []


def test_max_candidates_is_refused_with_an_explanation_of_the_fixed_cap():
    seen, handler = _recording()
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), {"species_entry_ref": REF, "target": EQUILIBRIUM, "max_candidates": 5})
    assert "fixed by the server" in str(caught.value.to_payload()["detail"])
    assert seen == []
