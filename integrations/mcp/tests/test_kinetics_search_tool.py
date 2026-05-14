"""Tests for ``tckdb_mcp.tools.kinetics_search``."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool, list_tools_payload
from tckdb_mcp.tools import kinetics_search as ks_tool

TOOL_NAME = "tckdb_search_kinetics"


def _make_client(handler) -> TCKDBHttpClient:
    transport = httpx.MockTransport(handler)
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1",
        api_key=None,
        timeout_seconds=5.0,
        transport=transport,
    )


def _stub_response(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "request": {
            "filter": {},
            "sort": "default",
            "collapse": "all",
            "include": ["provenance"],
        },
        "pagination": {"offset": 0, "limit": 25, "returned": 0, "total": 0},
        "records": records or [],
        "review_summary": {
            "approved": 0,
            "under_review": 0,
            "rejected": 0,
            "total": 0,
        },
    }


def _ok_handler(captured: list[dict[str, Any]]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "body": json.loads(request.content.decode("utf-8") or "null"),
                "headers": dict(request.headers),
            }
        )
        return httpx.Response(200, json=_stub_response())

    return handler


def _cfg() -> Config:
    return Config.from_env(env={})


# ---------------------------------------------------------------------------
# Registration / dispatch / URL / envelope
# ---------------------------------------------------------------------------


def test_tool_registered_in_list_tools_payload() -> None:
    names = [entry["name"] for entry in list_tools_payload()]
    assert TOOL_NAME in names


def test_dispatch_routes_to_kinetics_search_tool() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    result = dispatch_tool(
        TOOL_NAME, {"reactants": ["[OH]", "CC"]}, client, _cfg()
    )
    assert captured[0]["url"] == (
        "http://127.0.0.1:8010/api/v1/scientific/kinetics/search"
    )
    assert "records" in result
    client.close()


def test_post_url_resolves_under_api_v1() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"reactants": ["[OH]"]})
    assert captured[0]["method"] == "POST"
    assert captured[0]["path"] == "/api/v1/scientific/kinetics/search"
    client.close()


def test_response_passed_through_unchanged() -> None:
    fixture = _stub_response(
        records=[{"reaction_entry_ref": "rxe_aaa", "model_kind": "arrhenius"}]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _make_client(handler)
    result = ks_tool.run(client, _cfg(), {"family": "H_Abstraction"})
    assert result == fixture
    client.close()


# ---------------------------------------------------------------------------
# Discriminator requirement
# ---------------------------------------------------------------------------


def test_rejects_no_discriminator() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {})
    assert excinfo.value.code == "invalid_input"
    assert "discriminator" in excinfo.value.detail
    client.close()


def test_rejects_modifier_only_request() -> None:
    """Pure modifiers (direction, temperature, pressure, model_kind, review filters)
    are not a search."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client,
            _cfg(),
            {
                "direction": "forward",
                "temperature_min": 300,
                "temperature_max": 2000,
                "pressure": 1.0,
                "model_kind": "arrhenius",
                "min_review_status": "approved",
            },
        )
    assert "discriminator" in excinfo.value.detail
    client.close()


def test_direction_alone_rejected_as_modifier_only() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"direction": "forward"})
    assert "discriminator" in excinfo.value.detail
    client.close()


def test_empty_reactants_or_products_does_not_count_as_discriminator() -> None:
    """Empty list is rejected by the smiles-list validator before discriminator check."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"reactants": []})
    assert "empty list" in excinfo.value.detail
    client.close()


def test_reactants_alone_is_a_discriminator() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"reactants": ["[OH]", "CC"]})
    assert captured[0]["body"]["reactants"] == ["[OH]", "CC"]
    client.close()


def test_products_alone_is_a_discriminator() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"products": ["O"]})
    assert captured[0]["body"]["products"] == ["O"]
    client.close()


def test_reactants_and_products_together_accepted() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client,
        _cfg(),
        {"reactants": ["[OH]", "CC"], "products": ["O", "[CH2]C"]},
    )
    body = captured[0]["body"]
    assert body["reactants"] == ["[OH]", "CC"]
    assert body["products"] == ["O", "[CH2]C"]
    client.close()


def test_reaction_ref_alone_is_a_discriminator() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"reaction_ref": "rxn_01HZA"})
    assert captured[0]["body"]["reaction_ref"] == "rxn_01HZA"
    client.close()


def test_reaction_entry_ref_alone_is_a_discriminator() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"reaction_entry_ref": "rxe_01HZB"})
    assert captured[0]["body"]["reaction_entry_ref"] == "rxe_01HZB"
    client.close()


def test_family_alone_is_a_discriminator() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"family": "H_Abstraction"})
    assert captured[0]["body"]["family"] == "H_Abstraction"
    client.close()


# ---------------------------------------------------------------------------
# Reactants / products list validation
# ---------------------------------------------------------------------------


def test_rejects_reactants_not_a_list() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"reactants": "[OH]"})
    assert "must be a list" in excinfo.value.detail
    client.close()


def test_rejects_products_not_a_list() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(client, _cfg(), {"products": "O"})
    client.close()


def test_rejects_empty_string_in_reactants() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(client, _cfg(), {"reactants": ["CC", ""]})
    client.close()


def test_rejects_non_string_element_in_reactants() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(client, _cfg(), {"reactants": ["CC", 42]})
    client.close()


def test_valid_reactants_products_payload_forwarded_unchanged() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client,
        _cfg(),
        {"reactants": ["[OH]", "CC"], "products": ["O", "[CH2]C"]},
    )
    body = captured[0]["body"]
    # SMILES not canonicalized by the MCP.
    assert body["reactants"] == ["[OH]", "CC"]
    assert body["products"] == ["O", "[CH2]C"]
    client.close()


# ---------------------------------------------------------------------------
# Public-ref prefix validation
# ---------------------------------------------------------------------------


def test_rejects_reaction_ref_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"reaction_ref": "rxe_01HZA"})
    assert "rxn_" in excinfo.value.detail
    client.close()


def test_rejects_reaction_entry_ref_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"reaction_entry_ref": "rxn_01HZA"})
    assert "rxe_" in excinfo.value.detail
    client.close()


def test_rejects_level_of_theory_ref_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client,
            _cfg(),
            {"reactants": ["[OH]"], "level_of_theory_ref": "rxe_01HZA"},
        )
    assert "lot_" in excinfo.value.detail
    client.close()


def test_valid_lot_ref_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client,
        _cfg(),
        {"reactants": ["[OH]"], "level_of_theory_ref": "lot_b3lyp_6311g"},
    )
    assert captured[0]["body"]["level_of_theory_ref"] == "lot_b3lyp_6311g"
    client.close()


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("reaction_ref", "42"), ("reaction_entry_ref", "42")],
)
def test_rejects_integer_shaped_refs(field: str, bad_value: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(client, _cfg(), {field: bad_value})
    client.close()


# ---------------------------------------------------------------------------
# Integer-ID teaching errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "reaction_id",
        "reaction_entry_id",
        "species_id",
        "species_entry_id",
        "kinetics_id",
        "level_of_theory_id",
        "calculation_id",
    ],
)
def test_rejects_integer_id_fields_with_teaching_error(field: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"family": "H_Abstraction", field: 7})
    assert excinfo.value.code == "invalid_input"
    assert "integer-id" in excinfo.value.detail
    assert "public ref" in excinfo.value.detail.lower()
    client.close()


def test_integer_id_rejection_runs_before_unknown_field_check() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"reaction_id": 1, "frobnicate": True})
    assert "integer-id" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Off-endpoint / unsupported field rejection
# ---------------------------------------------------------------------------


def test_rejects_unknown_field() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"reactants": ["[OH]"], "frobnicate": True})
    assert "unknown field" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "field",
    [
        # Species-side identifiers — wrong endpoint
        "smiles",
        "inchi",
        "inchi_key",
        "formula",
        "species_ref",
        "species_entry_ref",
        # Geometry-side ref
        "geometry_ref",
        # Server-side rejected v0
        "sort",
        # Speculative unit fields that don't exist
        "pressure_unit",
        "temperature_unit",
    ],
)
def test_rejects_off_endpoint_fields(field: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], field: "x"}
        )
    assert "unknown field" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Direction enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", ["forward", "reverse", "either"])
def test_legal_direction_values_forwarded(direction: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client, _cfg(), {"reactants": ["[OH]", "CC"], "direction": direction}
    )
    assert captured[0]["body"]["direction"] == direction
    client.close()


def test_rejects_unknown_direction() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "direction": "exact"}
        )
    assert "direction" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Include tokens
# ---------------------------------------------------------------------------


def test_default_include_is_provenance() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"reactants": ["[OH]"]})
    assert captured[0]["body"]["include"] == ["provenance"]
    client.close()


def test_explicit_empty_include_overrides_default() -> None:
    """``include=[]`` reaches the server as ``[]`` (POST body convention)."""
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"reactants": ["[OH]"], "include": []})
    assert captured[0]["body"]["include"] == []
    client.close()


def test_rejects_internal_ids_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client,
            _cfg(),
            {"reactants": ["[OH]"], "include": ["internal_ids"]},
        )
    assert "internal_ids" in excinfo.value.detail
    client.close()


def test_rejects_unknown_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "include": ["bogus"]}
        )
    assert "bogus" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "token",
    ["statmech", "conformers", "thermo", "scans"],
)
def test_rejects_cross_endpoint_include_tokens(token: str) -> None:
    """Tokens legal elsewhere but NOT on kinetics search."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "include": [token]}
        )
    assert token in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "token",
    [
        "provenance",
        "calculations",
        "artifacts",
        "review",
        "species",
        "transition_states",
        "path_search",
        "irc",
        "all",
    ],
)
def test_each_legal_include_token_accepted(token: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client, _cfg(), {"reactants": ["[OH]"], "include": [token]}
    )
    assert captured[0]["body"]["include"] == [token]
    client.close()


def test_full_include_set_forwarded() -> None:
    full_set = sorted(ks_tool.LEGAL_INCLUDE_TOKENS - {"all"})
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client, _cfg(), {"reactants": ["[OH]"], "include": full_set}
    )
    assert captured[0]["body"]["include"] == full_set
    client.close()


def test_rejects_non_list_include() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "include": "provenance"}
        )
    client.close()


# ---------------------------------------------------------------------------
# Temperature / pressure filters
# ---------------------------------------------------------------------------


def test_temperature_filters_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client,
        _cfg(),
        {
            "reactants": ["[OH]"],
            "temperature_min": 300.0,
            "temperature_max": 2000.0,
        },
    )
    body = captured[0]["body"]
    assert body["temperature_min"] == 300.0
    assert body["temperature_max"] == 2000.0
    client.close()


def test_pressure_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client,
        _cfg(),
        {"reactants": ["[OH]"], "pressure": 1.0},
    )
    assert captured[0]["body"]["pressure"] == 1.0
    client.close()


def test_rejects_zero_temperature_min() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "temperature_min": 0}
        )
    assert "> 0" in excinfo.value.detail
    client.close()


def test_rejects_negative_temperature() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "temperature_min": -50}
        )
    client.close()


def test_rejects_zero_pressure() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(client, _cfg(), {"reactants": ["[OH]"], "pressure": 0})
    client.close()


def test_rejects_negative_pressure() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(client, _cfg(), {"reactants": ["[OH]"], "pressure": -1})
    client.close()


def test_rejects_non_numeric_temperature() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "temperature_min": "hot"}
        )
    client.close()


def test_rejects_bool_pressure() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(client, _cfg(), {"reactants": ["[OH]"], "pressure": True})
    client.close()


def test_rejects_temperature_min_greater_than_max() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client,
            _cfg(),
            {
                "reactants": ["[OH]"],
                "temperature_min": 2000,
                "temperature_max": 300,
            },
        )
    assert "<= temperature_max" in excinfo.value.detail
    client.close()


def test_temperature_min_equal_to_max_is_allowed() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client,
        _cfg(),
        {
            "reactants": ["[OH]"],
            "temperature_min": 1000,
            "temperature_max": 1000,
        },
    )
    body = captured[0]["body"]
    assert body["temperature_min"] == 1000.0
    assert body["temperature_max"] == 1000.0
    client.close()


# ---------------------------------------------------------------------------
# model_kind enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["arrhenius", "modified_arrhenius"])
def test_legal_model_kinds_forwarded(kind: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client, _cfg(), {"reactants": ["[OH]"], "model_kind": kind}
    )
    assert captured[0]["body"]["model_kind"] == kind
    client.close()


def test_rejects_unknown_model_kind() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "model_kind": "troe"}
        )
    assert "model_kind" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Pagination / collapse / review filters / software
# ---------------------------------------------------------------------------


def test_limit_capped_to_max() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"reactants": ["[OH]"], "limit": 10_000})
    assert captured[0]["body"]["limit"] == 50
    client.close()


def test_default_limit_applied_when_omitted() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"reactants": ["[OH]"]})
    assert captured[0]["body"]["limit"] == 25
    client.close()


def test_rejects_negative_offset() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(client, _cfg(), {"reactants": ["[OH]"], "offset": -1})
    client.close()


def test_offset_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(client, _cfg(), {"reactants": ["[OH]"], "offset": 7})
    assert captured[0]["body"]["offset"] == 7
    client.close()


@pytest.mark.parametrize("collapse", ["all", "first"])
def test_legal_collapse_values_forwarded(collapse: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client, _cfg(), {"reactants": ["[OH]"], "collapse": collapse}
    )
    assert captured[0]["body"]["collapse"] == collapse
    client.close()


def test_rejects_unknown_collapse_value() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ks_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "collapse": "every"}
        )
    client.close()


def test_review_filters_passed_as_json_booleans() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client,
        _cfg(),
        {
            "reactants": ["[OH]"],
            "min_review_status": "approved",
            "include_rejected": False,
            "include_deprecated": True,
        },
    )
    body = captured[0]["body"]
    assert body["min_review_status"] == "approved"
    # POST body: booleans stay as JSON booleans, NOT strings.
    assert body["include_rejected"] is False
    assert body["include_deprecated"] is True
    client.close()


def test_software_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ks_tool.run(
        client, _cfg(), {"reactants": ["[OH]"], "software": "gaussian"}
    )
    assert captured[0]["body"]["software"] == "gaussian"
    client.close()


# ---------------------------------------------------------------------------
# Error envelope mapping
# ---------------------------------------------------------------------------


def test_server_422_maps_to_invalid_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": "missing_identifier: at least one of {...} is required",
                "code": "missing_identifier",
            },
        )

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"family": "H_Abstraction"})
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.http_status == 422
    client.close()


def test_server_404_maps_to_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "no kinetics found"})

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        ks_tool.run(client, _cfg(), {"reaction_entry_ref": "rxe_missing"})
    assert excinfo.value.code == "not_found"
    client.close()


# ---------------------------------------------------------------------------
# Header propagation
# ---------------------------------------------------------------------------


def test_no_api_key_header_when_unset() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json=_stub_response())

    client = _make_client(handler)
    ks_tool.run(client, _cfg(), {"reactants": ["[OH]"]})
    assert "x-api-key" not in {k.lower() for k in seen[0]}
    client.close()


def test_api_key_header_forwarded_when_set() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json=_stub_response())

    transport = httpx.MockTransport(handler)
    client = TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1",
        api_key="tck_xyz",
        timeout_seconds=5.0,
        transport=transport,
    )
    ks_tool.run(client, _cfg(), {"reactants": ["[OH]"]})
    assert seen[0].get("x-api-key") == "tck_xyz"
    client.close()
