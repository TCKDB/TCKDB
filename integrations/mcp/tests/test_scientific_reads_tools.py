"""Tests for current scientific read/search MCP wrappers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool, list_tools_payload
from tckdb_mcp.tools import scientific_reads as tools


def _make_client(handler) -> TCKDBHttpClient:
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1",
        api_key=None,
        timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )


def _cfg() -> Config:
    return Config.from_env(env={})


def _capture_handler(captured: list[dict[str, Any]], response: dict[str, Any] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "method": request.method,
                "path": request.url.path,
                "query": dict(request.url.params),
                "body": json.loads(request.content.decode("utf-8") or "null"),
            }
        )
        return httpx.Response(200, json=response or {"records": []})

    return handler


def test_new_tools_are_registered() -> None:
    names = {entry["name"] for entry in list_tools_payload()}
    assert "tckdb_species_structure_search" in names
    assert "tckdb_calculation_search" in names
    assert "tckdb_network_kinetics_detail" in names
    assert "tckdb_literature_records" in names
    assert "tckdb_artifact_search" in names


@pytest.mark.parametrize(
    ("tool_name", "args", "expected_path"),
    [
        ("tckdb_species_structure_search", {"query_smiles": "CCO"}, "/api/v1/scientific/species/structure-search"),
        ("tckdb_calculation_search", {"calculation_type": "sp"}, "/api/v1/scientific/calculations/search"),
        ("tckdb_transition_state_search", {"transition_state_ref": "ts_abc"}, "/api/v1/scientific/transition-states/search"),
        ("tckdb_conformer_search", {"conformer_group_ref": "cg_abc"}, "/api/v1/scientific/conformers/search"),
        ("tckdb_statmech_search", {"statmech_ref": "sm_abc"}, "/api/v1/scientific/statmech/search"),
        ("tckdb_transport_search", {"transport_ref": "trn_abc"}, "/api/v1/scientific/transport/search"),
        ("tckdb_network_search", {"network_ref": "net_abc"}, "/api/v1/scientific/networks/search"),
        ("tckdb_network_solve_search", {"network_solve_ref": "nsolve_abc"}, "/api/v1/scientific/network-solves/search"),
        ("tckdb_network_kinetics_search", {"network_kinetics_ref": "nkin_abc"}, "/api/v1/scientific/network-kinetics/search"),
        ("tckdb_artifact_search", {"calculation_ref": "calc_abc"}, "/api/v1/scientific/artifacts/search"),
        ("tckdb_frequency_scale_factor_search", {"frequency_scale_factor_ref": "fsf_abc"}, "/api/v1/scientific/frequency-scale-factors/search"),
        ("tckdb_energy_correction_scheme_search", {"energy_correction_scheme_ref": "ecs_abc"}, "/api/v1/scientific/energy-correction-schemes/search"),
    ],
)
def test_search_tools_post_expected_paths(tool_name: str, args: dict[str, Any], expected_path: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_capture_handler(captured))
    dispatch_tool(tool_name, args, client, _cfg())
    assert captured[0]["method"] == "POST"
    assert captured[0]["path"] == expected_path
    assert captured[0]["body"]["limit"] == 25
    client.close()


@pytest.mark.parametrize(
    ("tool_name", "args", "expected_path"),
    [
        ("tckdb_calculation_detail", {"calculation_ref": "calc_abc"}, "/api/v1/scientific/calculations/calc_abc"),
        ("tckdb_transition_state_detail", {"transition_state_ref": "ts_abc"}, "/api/v1/scientific/transition-states/ts_abc"),
        ("tckdb_transition_state_entry_detail", {"transition_state_entry_ref": "tse_abc"}, "/api/v1/scientific/transition-state-entries/tse_abc"),
        ("tckdb_conformer_group_detail", {"conformer_group_ref": "cg_abc"}, "/api/v1/scientific/conformer-groups/cg_abc"),
        ("tckdb_conformer_observation_detail", {"conformer_observation_ref": "co_abc"}, "/api/v1/scientific/conformer-observations/co_abc"),
        ("tckdb_statmech_detail", {"statmech_ref": "sm_abc"}, "/api/v1/scientific/statmech/sm_abc"),
        ("tckdb_transport_detail", {"transport_ref": "trn_abc"}, "/api/v1/scientific/transport/trn_abc"),
        ("tckdb_network_detail", {"network_ref": "net_abc"}, "/api/v1/scientific/networks/net_abc"),
        ("tckdb_network_solve_detail", {"network_solve_ref": "nsolve_abc"}, "/api/v1/scientific/network-solves/nsolve_abc"),
        ("tckdb_network_kinetics_detail", {"network_kinetics_ref": "nkin_abc"}, "/api/v1/scientific/network-kinetics/nkin_abc"),
        ("tckdb_literature_records", {"literature_ref": "lit_abc"}, "/api/v1/scientific/literature/lit_abc/records"),
    ],
)
def test_detail_tools_get_expected_paths(tool_name: str, args: dict[str, Any], expected_path: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_capture_handler(captured))
    dispatch_tool(tool_name, args, client, _cfg())
    assert captured[0]["method"] == "GET"
    assert captured[0]["path"] == expected_path
    client.close()


def test_structure_search_uses_structure_endpoint_and_serializes_fields() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_capture_handler(captured))
    dispatch_tool(
        "tckdb_species_structure_search",
        {"query_smarts": "[OH]", "mode": "substructure", "limit": 10, "include": ["review"]},
        client,
        _cfg(),
    )
    assert captured[0]["path"] == "/api/v1/scientific/species/structure-search"
    assert captured[0]["body"]["query_smarts"] == "[OH]"
    assert captured[0]["body"]["mode"] == "substructure"
    assert captured[0]["body"]["include"] == ["review"]
    assert captured[0]["body"]["limit"] == 10
    client.close()


def test_include_internal_ids_rejected_and_not_in_schemas() -> None:
    for entry in list_tools_payload():
        assert "internal_ids" not in _schema_enums(entry["inputSchema"])

    client = _make_client(_capture_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        dispatch_tool("tckdb_network_kinetics_detail", {"network_kinetics_ref": "nkin_abc", "include": ["internal_ids"]}, client, _cfg())
    assert excinfo.value.code == "invalid_input"
    assert "internal_ids" in excinfo.value.detail
    client.close()


def test_artifact_search_strips_body_and_download_fields() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(
        _capture_handler(
            captured,
            {
                "records": [
                    {
                        "artifact_ref": "art_1",
                        "uri": "s3://private-bucket/key",
                        "body": "raw",
                        "download_url": "https://example.test/download",
                        "presigned_url": "https://example.test/signed",
                    }
                ]
            },
        )
    )
    result = dispatch_tool("tckdb_artifact_search", {"calculation_ref": "calc_abc", "include": ["calculation"]}, client, _cfg())
    assert captured[0]["path"] == "/api/v1/scientific/artifacts/search"
    assert captured[0]["body"]["include"] == ["calculation"]
    record = result["records"][0]
    assert record["uri"] == "s3://private-bucket/key"
    assert "body" not in record
    assert "download_url" not in record
    assert "presigned_url" not in record
    client.close()


def test_network_kinetics_points_are_explicit_only() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_capture_handler(captured))
    dispatch_tool("tckdb_network_kinetics_detail", {"network_kinetics_ref": "nkin_abc"}, client, _cfg())
    assert "points" not in captured[0]["query"].values()

    dispatch_tool(
        "tckdb_network_kinetics_detail",
        {"network_kinetics_ref": "nkin_abc", "include": ["points"]},
        client,
        _cfg(),
    )
    assert captured[1]["query"]["include"] == "points"
    client.close()


def test_network_kinetics_plog_response_is_forwarded_unchanged() -> None:
    response = {
        "network_kinetics_ref": "nkin_abc",
        "plog": [{"pressure_bar": 1.0, "arrhenius": {"a": 1.0}}],
        "plog_count_total": 1,
        "plog_truncated": False,
    }
    client = _make_client(_capture_handler([], response))
    result = dispatch_tool("tckdb_network_kinetics_detail", {"network_kinetics_ref": "nkin_abc", "include": ["plog"]}, client, _cfg())
    assert result == response
    client.close()


def test_literature_records_hits_inverse_records_path() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_capture_handler(captured))
    dispatch_tool("tckdb_literature_records", {"literature_ref": "lit_abc", "record_type": "calculation"}, client, _cfg())
    assert captured[0]["path"] == "/api/v1/scientific/literature/lit_abc/records"
    assert captured[0]["query"]["record_type"] == "calculation"
    client.close()


def test_hardcoded_paths_exist_in_openapi_golden() -> None:
    openapi_path = Path(__file__).resolve().parents[3] / "backend" / "tests" / "api" / "golden" / "openapi.json"
    data = json.loads(openapi_path.read_text())
    paths = set(data["paths"])
    for tool in tools.SEARCH_TOOLS.values():
        assert f"/api/v1{tool.path}" in paths
    for tool in tools.DETAIL_TOOLS.values():
        assert f"/api/v1{tool.path_template.format(ref='{' + _path_param_name(tool.name) + '}')}" in paths


def _path_param_name(tool_name: str) -> str:
    return {
        "tckdb_calculation_detail": "calculation_ref_or_id",
        "tckdb_transition_state_detail": "transition_state_ref_or_id",
        "tckdb_transition_state_entry_detail": "transition_state_entry_ref_or_id",
        "tckdb_conformer_group_detail": "conformer_group_ref_or_id",
        "tckdb_conformer_observation_detail": "conformer_observation_ref_or_id",
        "tckdb_statmech_detail": "statmech_ref_or_id",
        "tckdb_transport_detail": "transport_ref_or_id",
        "tckdb_network_detail": "network_ref_or_id",
        "tckdb_network_solve_detail": "network_solve_ref_or_id",
        "tckdb_network_kinetics_detail": "network_kinetics_ref_or_id",
        "tckdb_literature_records": "literature_ref_or_id",
    }[tool_name]


def _schema_enums(schema: Any) -> set[str]:
    if isinstance(schema, dict):
        values: set[str] = set()
        enum = schema.get("enum")
        if isinstance(enum, list):
            values.update(v for v in enum if isinstance(v, str))
        for child in schema.values():
            values.update(_schema_enums(child))
        return values
    if isinstance(schema, list):
        values: set[str] = set()
        for child in schema:
            values.update(_schema_enums(child))
        return values
    return set()
