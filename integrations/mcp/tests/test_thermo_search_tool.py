"""Tests for ``tckdb_mcp.tools.thermo_search``."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool, list_tools_payload
from tckdb_mcp.tools import thermo_search as ts_tool

TOOL_NAME = "tckdb_search_thermo"


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
        "species_entry_id": 0,
        "species_entry_ref": "spe_stub",
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
# Registration / dispatch / URL
# ---------------------------------------------------------------------------


def test_tool_registered_in_list_tools_payload() -> None:
    names = [entry["name"] for entry in list_tools_payload()]
    assert TOOL_NAME in names


def test_dispatch_routes_to_thermo_search_tool() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    result = dispatch_tool(TOOL_NAME, {"smiles": "CCO"}, client, _cfg())
    assert captured[0]["url"] == (
        "http://127.0.0.1:8010/api/v1/scientific/thermo/search"
    )
    assert "records" in result
    client.close()


def test_post_url_resolves_under_api_v1() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO"})
    assert captured[0]["method"] == "POST"
    assert captured[0]["path"] == "/api/v1/scientific/thermo/search"
    client.close()


def test_response_passed_through_unchanged() -> None:
    fixture = _stub_response(
        records=[{"thermo_ref": "thm_abc", "model_kind": "nasa"}]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _make_client(handler)
    result = ts_tool.run(client, _cfg(), {"smiles": "CCO"})
    assert result == fixture
    client.close()


# ---------------------------------------------------------------------------
# Discriminator requirement
# ---------------------------------------------------------------------------


def test_rejects_no_discriminator() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {})
    assert excinfo.value.code == "invalid_input"
    assert "discriminator" in excinfo.value.detail
    client.close()


def test_rejects_modifier_only_request() -> None:
    """Pure modifiers (temperature, model_kind, review filters) are not a search."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(
            client,
            _cfg(),
            {
                "temperature_min": 298,
                "temperature_max": 1500,
                "model_kind": "nasa",
                "software": "rmg",
                "min_review_status": "approved",
            },
        )
    assert "discriminator" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("smiles", "CCO"),
        ("inchi", "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"),
        ("inchi_key", "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"),
        ("formula", "C2H6O"),
        ("species_ref", "spc_01HZ5K"),
        ("species_entry_ref", "spe_01HZ5K9X2A"),
    ],
)
def test_each_discriminator_alone_is_accepted(field: str, value: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {field: value})
    assert captured[0]["body"][field] == value
    client.close()


# ---------------------------------------------------------------------------
# Public-ref prefix validation
# ---------------------------------------------------------------------------


def test_rejects_species_ref_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"species_ref": "spe_01HZ"})
    assert "spc_" in excinfo.value.detail
    client.close()


def test_rejects_species_entry_ref_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"species_entry_ref": "spc_01HZ"})
    assert "spe_" in excinfo.value.detail
    client.close()


def test_rejects_level_of_theory_ref_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(
            client,
            _cfg(),
            {"smiles": "CCO", "level_of_theory_ref": "spe_01HZ"},
        )
    assert "lot_" in excinfo.value.detail
    client.close()


def test_valid_level_of_theory_ref_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(
        client,
        _cfg(),
        {"smiles": "CCO", "level_of_theory_ref": "lot_b3lyp_6311g"},
    )
    assert captured[0]["body"]["level_of_theory_ref"] == "lot_b3lyp_6311g"
    client.close()


def test_rejects_integer_shaped_species_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ts_tool.run(client, _cfg(), {"species_ref": "42"})
    client.close()


def test_rejects_integer_shaped_species_entry_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ts_tool.run(client, _cfg(), {"species_entry_ref": "42"})
    client.close()


# ---------------------------------------------------------------------------
# Integer-ID teaching errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "species_id",
        "species_entry_id",
        "thermo_id",
        "level_of_theory_id",
        "calculation_id",
    ],
)
def test_rejects_integer_id_fields_with_teaching_error(field: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"smiles": "CCO", field: 7})
    assert excinfo.value.code == "invalid_input"
    assert "integer-id" in excinfo.value.detail
    assert "public ref" in excinfo.value.detail.lower()
    client.close()


def test_integer_id_rejection_runs_before_unknown_field_check() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"species_id": 1, "frobnicate": True})
    assert "integer-id" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Off-endpoint / unsupported field rejection
# ---------------------------------------------------------------------------


def test_rejects_unknown_field() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"smiles": "CCO", "frobnicate": True})
    assert "unknown field" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "field",
    [
        # Reaction-side fields — wrong endpoint
        "reaction_ref",
        "reaction_entry_ref",
        "reactants",
        "products",
        "direction",
        # Kinetics-only filter
        "pressure",
        # Geometry-side field
        "geometry_ref",
        # Server-side rejected v0
        "sort",
    ],
)
def test_rejects_off_endpoint_fields(field: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"smiles": "CCO", field: "x"})
    assert "unknown field" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Include tokens
# ---------------------------------------------------------------------------


def test_default_include_is_provenance() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO"})
    assert captured[0]["body"]["include"] == ["provenance"]
    client.close()


def test_explicit_empty_include_overrides_default() -> None:
    """Explicit ``include=[]`` reaches the server as ``[]``, overriding the
    tool's ``["provenance"]`` default. The server treats an empty list as
    "no expansions" — same effect as omitting the field but preserves the
    agent's intent on the wire."""
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO", "include": []})
    assert captured[0]["body"]["include"] == []
    client.close()


def test_rejects_internal_ids_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(
            client, _cfg(), {"smiles": "CCO", "include": ["internal_ids"]}
        )
    assert "internal_ids" in excinfo.value.detail
    client.close()


def test_rejects_unknown_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"smiles": "CCO", "include": ["bogus"]})
    assert "bogus" in excinfo.value.detail
    client.close()


def test_rejects_cross_endpoint_include_tokens() -> None:
    """``transition_states`` is legal on kinetics but not on thermo search."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(
            client,
            _cfg(),
            {"smiles": "CCO", "include": ["transition_states"]},
        )
    assert "transition_states" in excinfo.value.detail
    client.close()


def test_rejects_statmech_include_token_not_in_thermo_search_vocab() -> None:
    """``statmech`` is legal on the entry-scoped thermo endpoint but not on thermo SEARCH."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(
            client, _cfg(), {"smiles": "CCO", "include": ["statmech"]}
        )
    assert "statmech" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "token", ["provenance", "calculations", "artifacts", "review", "all"]
)
def test_each_legal_include_token_accepted(token: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO", "include": [token]})
    assert captured[0]["body"]["include"] == [token]
    client.close()


def test_full_include_set_forwarded() -> None:
    full_set = sorted(ts_tool.LEGAL_INCLUDE_TOKENS - {"all"})
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO", "include": full_set})
    assert captured[0]["body"]["include"] == full_set
    client.close()


def test_rejects_non_list_include() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ts_tool.run(client, _cfg(), {"smiles": "CCO", "include": "provenance"})
    client.close()


# ---------------------------------------------------------------------------
# Temperature filters
# ---------------------------------------------------------------------------


def test_temperature_filters_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(
        client,
        _cfg(),
        {
            "smiles": "CCO",
            "temperature_min": 298.15,
            "temperature_max": 1500.0,
        },
    )
    body = captured[0]["body"]
    assert body["temperature_min"] == 298.15
    assert body["temperature_max"] == 1500.0
    client.close()


def test_rejects_non_numeric_temperature() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ts_tool.run(
            client, _cfg(), {"smiles": "CCO", "temperature_min": "hot"}
        )
    client.close()


def test_rejects_bool_temperature() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ts_tool.run(
            client, _cfg(), {"smiles": "CCO", "temperature_min": True}
        )
    client.close()


def test_rejects_zero_temperature_min() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"smiles": "CCO", "temperature_min": 0})
    assert "> 0" in excinfo.value.detail
    client.close()


def test_rejects_negative_temperature() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ts_tool.run(
            client, _cfg(), {"smiles": "CCO", "temperature_min": -50}
        )
    client.close()


def test_rejects_temperature_min_greater_than_max() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(
            client,
            _cfg(),
            {
                "smiles": "CCO",
                "temperature_min": 1500,
                "temperature_max": 298,
            },
        )
    assert "<= temperature_max" in excinfo.value.detail
    client.close()


def test_temperature_min_equal_to_max_is_allowed() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(
        client,
        _cfg(),
        {"smiles": "CCO", "temperature_min": 1000, "temperature_max": 1000},
    )
    body = captured[0]["body"]
    assert body["temperature_min"] == 1000.0
    assert body["temperature_max"] == 1000.0
    client.close()


# ---------------------------------------------------------------------------
# model_kind enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["nasa", "points", "scalar"])
def test_legal_model_kinds_forwarded(kind: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO", "model_kind": kind})
    assert captured[0]["body"]["model_kind"] == kind
    client.close()


def test_rejects_unknown_model_kind() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"smiles": "CCO", "model_kind": "wilhoit"})
    assert "model_kind" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Pagination / collapse / review filters
# ---------------------------------------------------------------------------


def test_limit_capped_to_max() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO", "limit": 10_000})
    assert captured[0]["body"]["limit"] == 50
    client.close()


def test_default_limit_applied_when_omitted() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO"})
    assert captured[0]["body"]["limit"] == 25
    client.close()


def test_rejects_negative_offset() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ts_tool.run(client, _cfg(), {"smiles": "CCO", "offset": -1})
    client.close()


def test_offset_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO", "offset": 7})
    assert captured[0]["body"]["offset"] == 7
    client.close()


@pytest.mark.parametrize("collapse", ["all", "first"])
def test_legal_collapse_values_forwarded(collapse: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(client, _cfg(), {"smiles": "CCO", "collapse": collapse})
    assert captured[0]["body"]["collapse"] == collapse
    client.close()


def test_rejects_unknown_collapse_value() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        ts_tool.run(client, _cfg(), {"smiles": "CCO", "collapse": "every"})
    client.close()


def test_review_filters_passed_as_json_booleans() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    ts_tool.run(
        client,
        _cfg(),
        {
            "smiles": "CCO",
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
    ts_tool.run(client, _cfg(), {"smiles": "CCO", "software": "gaussian"})
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
        ts_tool.run(client, _cfg(), {"smiles": "CCO"})
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.http_status == 422
    client.close()


def test_server_404_maps_to_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "no thermo found"})

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        ts_tool.run(client, _cfg(), {"smiles": "CCO"})
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
    ts_tool.run(client, _cfg(), {"smiles": "CCO"})
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
    ts_tool.run(client, _cfg(), {"smiles": "CCO"})
    assert seen[0].get("x-api-key") == "tck_xyz"
    client.close()
