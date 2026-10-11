"""Tests for ``tckdb_mcp.tools.statmech_selection``: the statmech selection tool.

Request construction and verbatim pass-through only; agreement with the live server is covered by
``backend/tests/api/scientific/test_api_statmech_select.py`` and with the Python client by
``clients/python/tests/test_statmech_selection.py``.
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

NAME = "tckdb_select_statmech_models"
SPECIES = "spe_01HZ5K9X2A"
TSE = "tse_01HZ5K9X2A"
TARGET = {
    "kind": "species_entry",
    "species_entry_ref": SPECIES,
    "scope": {"kind": "basin", "conformer_group_ref": "cg_01HZ5K9X2A"},
    "isotope_convention": "entry_documented_masses",
    "electronic_scope": "entry_state",
    "stereochemical_scope": "entry_identity",
}
ARGS = {"intent": "qualify", "task": "within_basin_thermal", "observable": "entropy", "target": TARGET}


def _client(handler) -> TCKDBHttpClient:
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1", api_key=None, timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )


def _recording(answer: dict[str, Any] | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer if answer is not None else {"outcome": "qualified_models"})

    return seen, handler


def _call(client, arguments: dict[str, Any]):
    return dispatch_tool(NAME, arguments, client, Config.from_env(env={}))


def test_the_tool_is_listed_with_a_closed_schema_and_says_how_to_report_it():
    listed = {t["name"]: t for t in list_tools_payload()}
    assert NAME in listed
    schema = listed[NAME]["inputSchema"]
    assert schema["additionalProperties"] is False and set(schema["required"]) == {"intent", "task", "target"}
    assert not {"bounds", "max_candidates", "limit", "offset", "sort", "rules"} & set(schema["properties"])
    description = listed[NAME]["description"]
    for word in ("outcome", "basis", "advisory", "imported_parameters_not_revalidated", "source_literature_unidentified", "notices"):
        assert word in description


def test_posts_exactly_what_was_stated_and_returns_the_answer_untouched():
    seen, handler = _recording({"outcome": "sole_eligible_candidate", "notices": ["n"], "extra": 1})
    args = {**ARGS, "conditions": {"temperature_domain_k": {"min": 298.15, "max": 500.0}}, "profile": "curated", "result_mode": "first"}
    with _client(handler) as client:
        out = _call(client, args)
    (request,) = seen
    assert request.method == "POST" and request.url.path.endswith("/scientific/statmech/select")
    assert dict(request.url.params) == {"profile": "curated"}
    assert json.loads(request.content) == {
        "intent": "qualify", "task": "within_basin_thermal", "observable": "entropy", "target": TARGET,
        "conditions": {"temperature_domain_k": {"min": 298.15, "max": 500.0}}, "result_mode": "first",
    }  # no default of ours
    assert out == {"outcome": "sole_eligible_candidate", "notices": ["n"], "extra": 1}


def test_a_null_argument_is_not_given_and_a_transition_state_target_is_accepted():
    seen, handler = _recording()
    ts = {"kind": "transition_state_entry", "transition_state_entry_ref": TSE, "scope": {"kind": "ts_determination"}}
    with _client(handler) as client:
        _call(client, {**ARGS, "target": ts, "observable": None, "population": None})
    assert json.loads(seen[0].content) == {"intent": "qualify", "task": "within_basin_thermal", "target": ts}


@pytest.mark.parametrize(
    "target",
    [
        {**TARGET, "species_entry_ref": 12},
        {**TARGET, "species_entry_ref": TSE},
        {**TARGET, "species_entry_ref": None},
        {**TARGET, "kind": "reaction_entry"},
        {"kind": "transition_state_entry", "transition_state_entry_ref": "ts_abc", "scope": {"kind": "ts_determination"}},
        {**TARGET, "scope": "basin"},
        "spe_abc",
    ],
)
def test_a_missing_integer_or_wrong_kind_of_ref_is_refused_before_any_request(target):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError) as exc:
        _call(client, {**ARGS, "target": target})
    assert exc.value.code == "invalid_input" and seen == []


@pytest.mark.parametrize("field", ["species_entry_id", "transition_state_entry_id", "statmech_id", "declaration_id", "calculation_id"])
def test_integer_id_fields_are_refused_with_a_teaching_error(field):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError) as exc:
        _call(client, {**ARGS, field: 1})
    assert "integer-id fields" in str(exc.value) and seen == []


@pytest.mark.parametrize("field", ["bounds", "max_candidates", "manifest_bytes", "limit", "offset", "page", "page_size", "cursor", "sort", "rules"])
def test_no_bound_page_sort_or_rule_can_be_carried(field):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError) as exc:
        _call(client, {**ARGS, field: 1})
    assert "not a request field" in str(exc.value) and seen == []


def test_unknown_fields_and_bad_enums_and_candidate_refs_are_refused():
    seen, handler = _recording()
    with _client(handler) as client:
        for bad in (
            {**ARGS, "mystery": 1},
            {**ARGS, "intent": "rank"},
            {**ARGS, "coverage_requirement": "all"},
            {**ARGS, "comparison_objective": "best"},
            {**ARGS, "profile": "other"},
            {**ARGS, "conditions": "hot"},
            {**ARGS, "reference_model_ref": "sm_abc"},
            {**ARGS, "population": {"kind": "model_revisions", "model_refs": ["spe_abc"]}},
            {k: v for k, v in ARGS.items() if k != "task"},
        ):
            with pytest.raises(MCPToolError):
                _call(client, bad)
    assert seen == []


def test_a_server_refusal_reaches_the_caller_as_the_servers_own_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"code": "statmech_selection_bound_exceeded", "detail": "too many", "context": {"bound": "candidates"}})

    with _client(handler) as client, pytest.raises(MCPToolError) as exc:
        _call(client, ARGS)
    assert exc.value.http_status == 422 and "statmech_selection_bound_exceeded" in json.dumps(exc.value.to_payload())
