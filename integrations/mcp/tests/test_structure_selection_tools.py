"""Tests for ``tckdb_mcp.tools.structure_selection``: the three structure selection tools.

Request construction and verbatim pass-through only; agreement with the live server is covered by
``backend/tests/api/scientific/test_api_structure_select.py`` and with the Python client by
``clients/python/tests/test_structure_selection.py``.
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
from tckdb_mcp.tools import structure_selection

CALCULATIONS = "tckdb_select_species_entry_calculations"
CONFORMERS = "tckdb_select_species_entry_conformers"
EVIDENCE = "tckdb_select_transition_state_entry_evidence"
SPECIES = "spe_01HZ5K9X2A"
TSE = "tse_01HZ5K9X2A"

CASES = [
    (CALCULATIONS, "species_entry_ref", SPECIES, f"/scientific/species-entries/{SPECIES}/calculations/select"),
    (CONFORMERS, "species_entry_ref", SPECIES, f"/scientific/species-entries/{SPECIES}/conformers/select"),
    (EVIDENCE, "transition_state_entry_ref", TSE, f"/scientific/transition-state-entries/{TSE}/evidence/select"),
]


def _client(handler) -> TCKDBHttpClient:
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1", api_key=None, timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )


def _recording(answer: dict[str, Any] | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer if answer is not None else {"outcome": "recorded_minimum"})

    return seen, handler


def _call(client, name: str, arguments: dict[str, Any]):
    return dispatch_tool(name, arguments, client, Config.from_env(env={}))


def test_the_three_tools_are_listed_with_closed_schemas():
    listed = {t["name"]: t for t in list_tools_payload()}
    assert {CALCULATIONS, CONFORMERS, EVIDENCE} <= set(listed)
    for name in (CALCULATIONS, CONFORMERS, EVIDENCE):
        schema = listed[name]["inputSchema"]
        assert schema["additionalProperties"] is False
        assert len(schema["required"]) == 1 and schema["required"][0].endswith("_ref")
        assert "bounds" not in schema["properties"] and "max_candidates" not in schema["properties"]
        assert "outcome" in listed[name]["description"] and "basis" in listed[name]["description"]
    assert "require_connectivity" in listed[EVIDENCE]["inputSchema"]["properties"]
    assert "require_connectivity" not in listed[CONFORMERS]["inputSchema"]["properties"]
    assert listed[EVIDENCE]["inputSchema"]["properties"]["intent"]["enum"] == ["validated_saddle", "qualify_evidence", "protocol_preferred"]


@pytest.mark.parametrize(("name", "ref_field", "ref", "path"), CASES)
def test_posts_an_empty_body_when_only_the_entry_is_given(name, ref_field, ref, path):
    seen, handler = _recording()
    with _client(handler) as client:
        out = _call(client, name, {ref_field: ref})
    (request,) = seen
    assert request.method == "POST" and request.url.path.endswith(path)
    assert json.loads(request.content) == {}
    assert out == {"outcome": "recorded_minimum"}  # the server's answer, untouched


def test_forwards_what_was_asked_and_drops_nothing_else():
    seen, handler = _recording({"outcome": "qualified_evidence", "basis": "b"})
    with _client(handler) as client:
        out = _call(
            client, EVIDENCE,
            {
                "transition_state_entry_ref": TSE, "intent": "qualify_evidence", "quantity": None,
                "validation_claim": "first_order_saddle", "require_connectivity": True, "result_mode": "first",
                "geometry_ref": "geom_abc", "member_refs": ["sdet_a", "sdet_b"], "permitted_quality": ["raw"],
                "coverage_requirement": "all_requested_members", "objective": None, "profile": "curated",
            },
        )
    assert json.loads(seen[0].content) == {
        "intent": "qualify_evidence", "quantity": None, "validation_claim": "first_order_saddle",
        "require_connectivity": True, "result_mode": "first", "geometry_ref": "geom_abc",
        "member_refs": ["sdet_a", "sdet_b"], "permitted_quality": ["raw"], "coverage_requirement": "all_requested_members",
    }  # quantity null is sent (no energy asked); a null objective is simply not given
    assert dict(seen[0].url.params) == {"profile": "curated"}
    assert out == {"outcome": "qualified_evidence", "basis": "b"}


def test_a_given_quantity_is_forwarded_and_an_absent_one_is_not_invented():
    seen, handler = _recording()
    with _client(handler) as client:
        _call(client, CALCULATIONS, {"species_entry_ref": SPECIES, "quantity": "zero_kelvin_energy"})
        _call(client, CALCULATIONS, {"species_entry_ref": SPECIES})
    assert json.loads(seen[0].content) == {"quantity": "zero_kelvin_energy"}
    assert json.loads(seen[1].content) == {}


@pytest.mark.parametrize(
    ("name", "ref_field", "bad"),
    [
        (CALCULATIONS, "species_entry_ref", 12),
        (CALCULATIONS, "species_entry_ref", TSE),  # a transition state entry ref on a species tool
        (CONFORMERS, "species_entry_ref", "rxe_abc"),
        (EVIDENCE, "transition_state_entry_ref", SPECIES),
        (EVIDENCE, "transition_state_entry_ref", "ts_abc"),  # the concept is not an entry
        (EVIDENCE, "transition_state_entry_ref", None),
    ],
)
def test_a_missing_integer_or_wrong_kind_of_ref_is_refused_before_any_request(name, ref_field, bad):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError) as exc:
        _call(client, name, {ref_field: bad})
    assert exc.value.code == "invalid_input" and seen == []


@pytest.mark.parametrize("field", ["species_entry_id", "transition_state_entry_id", "calculation_id", "geometry_id", "determination_id"])
def test_integer_id_fields_are_refused_with_a_teaching_error(field):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError) as exc:
        _call(client, CALCULATIONS, {"species_entry_ref": SPECIES, field: 1})
    assert "integer-id fields" in str(exc.value) and seen == []


@pytest.mark.parametrize("field", ["bounds", "max_candidates", "manifest_bytes", "limit", "offset", "page", "page_size", "cursor", "sort", "rules"])
def test_no_bound_page_sort_or_rule_can_be_carried(field):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError) as exc:
        _call(client, CONFORMERS, {"species_entry_ref": SPECIES, field: 5})
    assert "engineering bounds are fixed by the server" in str(exc.value) and seen == []


@pytest.mark.parametrize(
    ("name", "ref_field", "ref", "extra"),
    [
        (CALCULATIONS, "species_entry_ref", SPECIES, {"intent": "validated_minimum"}),  # not this tool's intent
        (CONFORMERS, "species_entry_ref", SPECIES, {"intent": "recorded_minimum"}),
        (EVIDENCE, "transition_state_entry_ref", TSE, {"intent": "validated_minimum"}),
        (CONFORMERS, "species_entry_ref", SPECIES, {"require_connectivity": True}),  # a transition-state field
        (CALCULATIONS, "species_entry_ref", SPECIES, {"coverage_requirement": "everything"}),
        (CALCULATIONS, "species_entry_ref", SPECIES, {"validation_claim": "a_minimum_probably"}),
        (CALCULATIONS, "species_entry_ref", SPECIES, {"apply_rules": "yes"}),
        (CALCULATIONS, "species_entry_ref", SPECIES, {"quantity": 5}),
        (CALCULATIONS, "species_entry_ref", SPECIES, {"member_refs": []}),
        (CALCULATIONS, "species_entry_ref", SPECIES, {"permitted_quality": ["pristine"]}),
        (CALCULATIONS, "species_entry_ref", SPECIES, {"geometry_ref": SPECIES}),  # the wrong kind of ref
        (CALCULATIONS, "species_entry_ref", SPECIES, {"recipe": "restricted"}),
        (CALCULATIONS, "species_entry_ref", SPECIES, {"profile": "gold"}),
        (CALCULATIONS, "species_entry_ref", SPECIES, {"no_such_field": 1}),
    ],
)
def test_a_malformed_argument_is_refused_before_any_request(name, ref_field, ref, extra):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError) as exc:
        _call(client, name, {ref_field: ref, **extra})
    assert exc.value.code == "invalid_input" and seen == []


def test_the_servers_refusal_reaches_the_agent_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"code": "structure_selection_unsupported", "context": {"reason": "deferred_quantity", "quantity": "enthalpy"}})

    with _client(handler) as client, pytest.raises(MCPToolError) as exc:
        _call(client, CALCULATIONS, {"species_entry_ref": SPECIES, "quantity": "enthalpy"})
    assert exc.value.http_status == 422 and "structure_selection_unsupported" in json.dumps(exc.value.to_payload())


def test_the_module_adds_no_prose_of_its_own_to_a_result():
    seen, handler = _recording({"outcome": "incomparable_alternatives", "basis": "server words", "selected_refs": []})
    with _client(handler) as client:
        out = _call(client, CALCULATIONS, {"species_entry_ref": SPECIES})
    assert set(out) == {"outcome", "basis", "selected_refs"}
    assert structure_selection.TOOL_NAMES == {CALCULATIONS, CONFORMERS, EVIDENCE}
