"""Tests for ``tckdb_mcp.tools.relative_conformer_energy``: the two relative conformer energy tools.

Request construction, prefix validation and verbatim pass-through only; agreement with the live server is covered by the
backend route tests and with the Python client by ``clients/python/tests/test_relative_conformer_energy.py``.
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

SINGLE = "tckdb_species_entry_relative_conformer_energies"
COMPARE = "tckdb_compare_relative_conformer_energies"
SPECIES = "spe_01HZ5K9X2A"
BASE = f"/scientific/species-entries/{SPECIES}/conformers/relative-energies"

SINGLE_ARGS: dict[str, Any] = {
    "species_entry_ref": SPECIES,
    "reference_conformer_ref": "cg_ref01",
    "energy_kind": "electronic_energy",
    "certificate": "local_minimum_any_surface",
    "level": {"level_of_theory_ref": "lot_abc01"},
}
COMPARE_ARGS: dict[str, Any] = {
    "species_entry_ref": SPECIES,
    "reference_conformer_ref": "cg_ref01",
    "energy_kind": "zero_kelvin_energy",
    "certificate": "local_minimum_on_energy_surface",
    "candidate": {"level": {"level_of_theory_ref": "lot_cand1"}},
    "reference": {"level": {"composite_scheme_ref": "csch_ref1"}},
    "pairing": "same_basin",
}


def _client(handler) -> TCKDBHttpClient:
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1", api_key=None, timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )


def _recording(answer: dict[str, Any] | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer if answer is not None else {"outcome": "relative_energies"})

    return seen, handler


def _call(client, name: str, arguments: dict[str, Any]):
    return dispatch_tool(name, arguments, client, Config.from_env(env={}))


def _listed() -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in list_tools_payload()}


def _without(args: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k != key}


def _no_default(node: Any) -> bool:
    """True if no ``default`` key appears anywhere under ``node``."""
    if isinstance(node, dict):
        return "default" not in node and all(_no_default(v) for v in node.values())
    if isinstance(node, list):
        return all(_no_default(v) for v in node)
    return True


def test_both_tools_are_listed_with_closed_schemas():
    listed = _listed()
    assert {SINGLE, COMPARE} <= set(listed)
    for name in (SINGLE, COMPARE):
        assert listed[name]["inputSchema"]["additionalProperties"] is False
        assert "bounds" not in listed[name]["inputSchema"]["properties"]


def test_required_fields_are_required_and_nothing_is_defaulted():
    # Kills: defaulting in the tool (a required choice absent from "required", or carrying a "default").
    listed = _listed()
    single = listed[SINGLE]["inputSchema"]
    assert {"species_entry_ref", "reference_conformer_ref", "energy_kind", "certificate"} <= set(single["required"])
    assert "level" in single["required"]
    compare = listed[COMPARE]["inputSchema"]
    assert {
        "species_entry_ref", "reference_conformer_ref", "energy_kind", "certificate", "candidate", "reference", "pairing"
    } <= set(compare["required"])
    for name in (SINGLE, COMPARE):
        schema = listed[name]["inputSchema"]
        for field in schema["required"]:
            assert "default" not in schema["properties"][field], field
        assert _no_default(schema)  # no optional field carries a tool-side default either


def test_no_tool_schema_has_a_top_level_combinator():
    # Kills: a top-level oneOf/anyOf/allOf, which the Claude API rejects in a tool input_schema.
    for tool in list_tools_payload():
        assert not {"oneOf", "anyOf", "allOf"} & set(tool["inputSchema"]), tool["name"]
    for name in (SINGLE, COMPARE):
        assert "oneOf" not in json.dumps(_listed()[name]["inputSchema"])


def test_descriptions_tell_the_model_what_to_quote_and_what_not_to_claim():
    listed = _listed()
    for name in (SINGLE, COMPARE):
        text = listed[name]["description"]
        assert "outcome" in text and "basis" in text and "claims.statement" in text and "verbatim" in text
    assert "DESCRIPTIVE ONLY" in listed[COMPARE]["description"]
    assert "depends on the reference conformer" in listed[COMPARE]["description"]


def test_single_posts_the_stated_question_only():
    seen, handler = _recording()
    with _client(handler) as client:
        out = _call(client, SINGLE, dict(SINGLE_ARGS))
    (request,) = seen
    assert request.method == "POST" and request.url.path.endswith(BASE)
    assert json.loads(request.content) == {
        "reference_conformer_ref": "cg_ref01",
        "energy_kind": "electronic_energy",
        "level": {"level_of_theory_ref": "lot_abc01"},
        "certificate": "local_minimum_any_surface",
    }
    assert out == {"outcome": "relative_energies"}  # the server's answer, untouched


def test_compare_posts_to_the_compare_route_with_both_protocols():
    seen, handler = _recording({"outcome": "compared"})
    with _client(handler) as client:
        out = _call(client, COMPARE, dict(COMPARE_ARGS))
    (request,) = seen
    assert request.url.path.endswith(BASE + "/compare")
    body = json.loads(request.content)
    assert body["pairing"] == "same_basin"
    assert body["candidate"] == {"level": {"level_of_theory_ref": "lot_cand1"}}
    assert body["reference"] == {"level": {"composite_scheme_ref": "csch_ref1"}}
    assert out == {"outcome": "compared"}


@pytest.mark.parametrize(
    ("name", "args", "field", "bad"),
    [
        (SINGLE, SINGLE_ARGS, "level", {"level_of_theory_ref": "cg_ref01"}),
        (SINGLE, SINGLE_ARGS, "reference_conformer_ref", "lot_abc01"),
        (SINGLE, SINGLE_ARGS, "species_entry_ref", "tse_01HZ5K9X2A"),
        (SINGLE, SINGLE_ARGS, "level", {"composite_scheme_ref": "lot_abc01"}),
        (COMPARE, COMPARE_ARGS, "reference_conformer_ref", "spe_01HZ5K9X2A"),
    ],
)
def test_a_wrong_prefix_is_refused_before_any_request(name, args, field, bad):
    # Kills: missing prefix validation.
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError):
        _call(client, name, {**args, field: bad})
    assert seen == []


def test_a_wrong_prefix_inside_a_compare_protocol_is_refused():
    seen, handler = _recording()
    bad = {**COMPARE_ARGS, "candidate": {"level": {"level_of_theory_ref": "cg_x"}}}
    with _client(handler) as client, pytest.raises(MCPToolError):
        _call(client, COMPARE, bad)
    assert seen == []


@pytest.mark.parametrize("missing", ["reference_conformer_ref", "energy_kind", "certificate", "species_entry_ref"])
@pytest.mark.parametrize("name,args", [(SINGLE, SINGLE_ARGS), (COMPARE, COMPARE_ARGS)])
def test_a_required_choice_is_never_filled_in(name, args, missing):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError):
        _call(client, name, _without(args, missing))
    assert seen == []


@pytest.mark.parametrize("missing", ["candidate", "reference", "pairing"])
def test_compare_requires_both_protocols_and_the_pairing(missing):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError):
        _call(client, COMPARE, _without(COMPARE_ARGS, missing))
    assert seen == []


@pytest.mark.parametrize(
    "level",
    [{"level_of_theory_ref": "lot_x", "composite_scheme_ref": "csch_x"}, {}, "lot_x", {"bogus": "lot_x"}],
)
def test_the_level_is_exactly_one_of_a_lot_and_a_csch(level):
    seen, handler = _recording()
    with _client(handler) as client:
        with pytest.raises(MCPToolError):
            _call(client, SINGLE, {**SINGLE_ARGS, "level": level})
        with pytest.raises(MCPToolError):
            _call(client, SINGLE, _without(SINGLE_ARGS, "level"))
    assert seen == []


@pytest.mark.parametrize("field", ["limit", "offset", "sort", "rules", "bounds", "rank"])
def test_no_bound_page_sort_or_rank_can_be_carried(field):
    seen, handler = _recording()
    with _client(handler) as client, pytest.raises(MCPToolError):
        _call(client, SINGLE, {**SINGLE_ARGS, field: 1})
    assert seen == []


def test_optional_fields_are_forwarded_when_given_and_absent_otherwise():
    seen, handler = _recording()
    with _client(handler) as client:
        _call(client, SINGLE, {**SINGLE_ARGS, "member_refs": ["cg_a", "cg_b"], "profile": "curated", "repeat_policy": None})
    (request,) = seen
    body = json.loads(request.content)
    assert body["member_refs"] == ["cg_a", "cg_b"]
    assert "repeat_policy" not in body and "coverage_requirement" not in body
    assert request.url.params["profile"] == "curated"


def test_package_version_matches_installed_metadata():
    from importlib.metadata import PackageNotFoundError, version

    import tckdb_mcp

    try:
        expected = version("tckdb-mcp")
    except PackageNotFoundError:
        expected = "0.0.0+local"
    assert tckdb_mcp.__version__ == expected
    assert tckdb_mcp.__version__ != "0.2.0"
