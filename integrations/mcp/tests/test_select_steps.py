"""The ``filters`` and ``eligibility`` options of the ``tckdb_select_*`` tools.

Request construction and verbatim pass-through only; what the server does with them is covered by
``backend/tests/api/scientific/test_select_filters_eligibility_*.py``.
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

SPECIES = "spe_01HZ5K9X2A"
STATMECH = ("tckdb_select_statmech_models", {
    "intent": "qualify", "task": "within_basin_thermal", "observable": "entropy",
    "target": {"kind": "species_entry", "species_entry_ref": SPECIES, "scope": {"kind": "basin", "conformer_group_ref": "cg_01HZ5K9X2A"}},
})
THERMO = ("tckdb_select_species_entry_thermo", {"species_entry_ref": SPECIES, "target": {"kind": "equilibrium_ensemble"}})

TOOLS = {
    # tool -> (a filter it offers, the eligibility defaults it offers, a default it must not offer)
    "tckdb_select_species_entry_thermo": ("level_of_theory_ref", {"include_rejected", "include_deprecated", "include_hard_failed_evidence", "include_confirmed_findings"}, None),
    "tckdb_select_reaction_entry_kinetics": ("level_of_theory_ref", {"include_rejected", "include_deprecated", "include_hard_failed_evidence", "include_confirmed_findings"}, None),
    "tckdb_select_network_kinetics": ("method", {"include_rejected", "include_deprecated"}, "include_confirmed_findings"),
    "tckdb_select_statmech_models": ("method", {"include_rejected", "include_deprecated", "include_confirmed_findings"}, "include_hard_failed_evidence"),
    "tckdb_select_species_entry_calculations": ("software", {"include_rejected", "include_deprecated", "include_confirmed_findings"}, "include_hard_failed_evidence"),
}


def _client(handler) -> TCKDBHttpClient:
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1", api_key=None, timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )


def _recording():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"outcome": "sole_eligible_candidate"})

    return seen, handler


def _call(name: str, arguments: dict[str, Any], client):
    return dispatch_tool(name, arguments, client, Config.from_env(env={}))


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_every_select_tool_lists_both_options_with_its_own_vocabulary(name):
    listed = {t["name"]: t for t in list_tools_payload()}
    props = listed[name]["inputSchema"]["properties"]
    filter_name, defaults, absent = TOOLS[name]
    assert filter_name in props["filters"]["properties"] and props["filters"]["additionalProperties"] is False
    assert set(props["eligibility"]["properties"]) == defaults
    if absent is not None:
        assert absent not in props["eligibility"]["properties"]


@pytest.mark.parametrize("args", [THERMO, STATMECH], ids=["thermo", "statmech"])
def test_the_options_are_forwarded_unchanged_and_nothing_else_is_added(args):
    name, base = args
    seen, handler = _recording()
    filters = {"software": "orca"} if name == THERMO[0] else {"method": "wb97xd", "software": "orca"}
    eligibility = {"include_rejected": True}
    out = _call(name, {**base, "filters": filters, "eligibility": eligibility}, _client(handler))
    assert out == {"outcome": "sole_eligible_candidate"}  # the server's answer, unchanged
    body = json.loads(seen[0].content)
    assert body["filters"] == filters and body["eligibility"] == eligibility


@pytest.mark.parametrize("args", [THERMO, STATMECH], ids=["thermo", "statmech"])
def test_options_not_stated_are_not_sent(args):
    name, base = args
    seen, handler = _recording()
    _call(name, dict(base), _client(handler))
    body = json.loads(seen[0].content)
    assert "filters" not in body and "eligibility" not in body


@pytest.mark.parametrize(
    "extra",
    [
        {"filters": {"coverage_temperature_min_k": "300"}},  # a filter the search does not offer
        {"filters": {"software": ""}},
        {"filters": "orca"},
        {"eligibility": {"include_everything": True}},
        {"eligibility": {"include_rejected": "yes"}},
        {"eligibility": {"include_hard_failed_evidence": True}},  # a statmech candidate has no evidence rubric
    ],
)
def test_a_wrong_option_is_a_teaching_error_before_any_request(extra):
    name, base = STATMECH
    seen, handler = _recording()
    with pytest.raises(MCPToolError):
        _call(name, {**base, **extra}, _client(handler))
    assert seen == []


@pytest.mark.parametrize("args", [THERMO, STATMECH], ids=["thermo", "statmech"])
def test_scientific_origin_is_offered_and_forwarded(args):
    """Kills: the tool refusing a filter its backend route takes."""
    name, base = args
    seen, handler = _recording()
    _call(name, {**base, "filters": {"scientific_origin": "computed"}}, _client(handler))
    assert json.loads(seen[0].content)["filters"] == {"scientific_origin": "computed"}
    assert "scientific_origin" in {t["name"]: t for t in list_tools_payload()}[name]["inputSchema"]["properties"]["filters"]["properties"]


@pytest.mark.parametrize("family", ["dft", ["dft", "composite"]])
def test_thermo_takes_method_family_as_a_name_or_a_list_and_the_rung_as_integers(family):
    """Kills: a string-only filter schema that refused a list of families or an integer rung."""
    name, base = THERMO
    seen, handler = _recording()
    filters = {"method_family": family, "dft_rung_min": 3, "dft_rung_max": 4}
    _call(name, {**base, "filters": filters}, _client(handler))
    assert json.loads(seen[0].content)["filters"] == filters


@pytest.mark.parametrize(
    "filters",
    [{"method_family": []}, {"method_family": [1]}, {"method_family": ""}, {"dft_rung_min": 0}, {"dft_rung_max": 6}, {"dft_rung_min": "4"}, {"dft_rung_min": True}],
)
def test_a_bad_family_or_rung_is_refused_before_any_call(filters):
    name, base = THERMO
    seen, handler = _recording()
    with pytest.raises(MCPToolError):
        _call(name, {**base, "filters": filters}, _client(handler))
    assert seen == []
