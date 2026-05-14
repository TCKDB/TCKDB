"""Tests for ``tckdb_mcp.tools.reactions`` and its dispatch wiring."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool, list_tools_payload
from tckdb_mcp.tools import reactions as reactions_tool


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
        "request": {"offset": 0, "limit": 25, "collapse": "all", "include": []},
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
                "body": json.loads(request.content.decode("utf-8") or "null"),
                "headers": dict(request.headers),
            }
        )
        return httpx.Response(200, json=_stub_response())

    return handler


def _cfg() -> Config:
    return Config.from_env(env={})


# ---------------------------------------------------------------------------
# Dispatch / registration
# ---------------------------------------------------------------------------


def test_tool_registered_in_list_tools_payload() -> None:
    names = [entry["name"] for entry in list_tools_payload()]
    assert "tckdb_search_reactions" in names


def test_dispatch_routes_to_reactions_tool() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    result = dispatch_tool(
        "tckdb_search_reactions",
        {"reactants": ["[OH]", "CC"]},
        client,
        _cfg(),
    )
    assert captured[0]["url"] == (
        "http://127.0.0.1:8010/api/v1/scientific/reactions/search"
    )
    assert "records" in result
    client.close()


# ---------------------------------------------------------------------------
# URL routing
# ---------------------------------------------------------------------------


def test_reactions_search_url_under_api_v1() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(client, _cfg(), {"reactants": ["[OH]", "CC"]})
    assert captured[0]["method"] == "POST"
    assert captured[0]["url"] == (
        "http://127.0.0.1:8010/api/v1/scientific/reactions/search"
    )
    client.close()


# ---------------------------------------------------------------------------
# Discriminator requirement
# ---------------------------------------------------------------------------


def test_rejects_when_no_discriminator() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(client, _cfg(), {})
    assert excinfo.value.code == "invalid_input"
    assert "discriminator" in excinfo.value.detail
    client.close()


def test_rejects_when_only_modifiers_supplied() -> None:
    """``direction`` and ``min_review_status`` alone are not a search."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        reactions_tool.run(
            client,
            _cfg(),
            {"direction": "forward", "min_review_status": "approved"},
        )
    client.close()


def test_family_alone_is_a_valid_discriminator() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(client, _cfg(), {"family": "H_Abstraction"})
    assert captured[0]["body"]["family"] == "H_Abstraction"
    client.close()


# ---------------------------------------------------------------------------
# Reactants / products list validation
# ---------------------------------------------------------------------------


def test_valid_reactants_products_payload_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(
        client,
        _cfg(),
        {"reactants": ["[OH]", "CC"], "products": ["O", "[CH2]C"]},
    )
    body = captured[0]["body"]
    assert body["reactants"] == ["[OH]", "CC"]
    assert body["products"] == ["O", "[CH2]C"]
    client.close()


def test_rejects_reactants_not_a_list() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(client, _cfg(), {"reactants": "[OH]"})
    assert excinfo.value.code == "invalid_input"
    assert "must be a list" in excinfo.value.detail
    client.close()


def test_rejects_products_not_a_list() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        reactions_tool.run(client, _cfg(), {"products": "O"})
    client.close()


def test_rejects_empty_reactants_list() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(client, _cfg(), {"reactants": []})
    assert excinfo.value.code == "invalid_input"
    assert "empty list" in excinfo.value.detail
    client.close()


def test_rejects_empty_products_list() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        reactions_tool.run(client, _cfg(), {"products": []})
    client.close()


def test_rejects_empty_string_in_reactants() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        reactions_tool.run(client, _cfg(), {"reactants": ["CC", ""]})
    client.close()


def test_rejects_non_string_element_in_reactants() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        reactions_tool.run(client, _cfg(), {"reactants": ["CC", 42]})
    client.close()


# ---------------------------------------------------------------------------
# Ref validation
# ---------------------------------------------------------------------------


def test_valid_reaction_ref_accepted() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(client, _cfg(), {"reaction_ref": "rxn_01HZA"})
    assert captured[0]["body"]["reaction_ref"] == "rxn_01HZA"
    client.close()


def test_valid_reaction_entry_ref_accepted() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(client, _cfg(), {"reaction_entry_ref": "rxe_01HZB"})
    assert captured[0]["body"]["reaction_entry_ref"] == "rxe_01HZB"
    client.close()


def test_rejects_reaction_ref_with_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(client, _cfg(), {"reaction_ref": "rxe_01HZA"})
    assert excinfo.value.code == "invalid_input"
    assert "rxn_" in excinfo.value.detail
    client.close()


def test_rejects_reaction_entry_ref_with_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(client, _cfg(), {"reaction_entry_ref": "rxn_01HZA"})
    assert excinfo.value.code == "invalid_input"
    assert "rxe_" in excinfo.value.detail
    client.close()


def test_rejects_integer_shaped_reaction_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        reactions_tool.run(client, _cfg(), {"reaction_ref": "42"})
    client.close()


# ---------------------------------------------------------------------------
# Integer-ID teaching errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["reaction_id", "reaction_entry_id", "species_id", "species_entry_id"],
)
def test_rejects_integer_id_fields_with_teaching_error(field: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(client, _cfg(), {"family": "H_Abstraction", field: 42})
    assert excinfo.value.code == "invalid_input"
    assert "integer-id" in excinfo.value.detail
    assert "reaction_ref" in excinfo.value.detail
    client.close()


def test_integer_id_rejection_runs_before_unknown_field() -> None:
    """Teaching message must win over the generic unknown-field error."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(client, _cfg(), {"reaction_id": 7, "frobnicate": True})
    assert "integer-id" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Direction enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", ["forward", "reverse", "either"])
def test_legal_direction_values_forwarded(direction: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(
        client, _cfg(), {"reactants": ["[OH]", "CC"], "direction": direction}
    )
    assert captured[0]["body"]["direction"] == direction
    client.close()


def test_rejects_unknown_direction() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "direction": "exact"}
        )
    assert excinfo.value.code == "invalid_input"
    assert "direction" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Include tokens
# ---------------------------------------------------------------------------


def test_rejects_internal_ids_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(
            client,
            _cfg(),
            {"family": "H_Abstraction", "include": ["internal_ids"]},
        )
    assert excinfo.value.code == "invalid_input"
    assert "internal_ids" in excinfo.value.detail
    client.close()


def test_rejects_unknown_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(
            client, _cfg(), {"family": "H_Abstraction", "include": ["bogus"]}
        )
    assert excinfo.value.code == "invalid_input"
    assert "bogus" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "tokens",
    [
        ["kinetics"],
        ["transition_states"],
        ["species"],
        ["review"],
        ["all"],
        ["kinetics", "transition_states", "species"],
    ],
)
def test_legal_include_tokens_forwarded(tokens: list[str]) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(
        client, _cfg(), {"reactants": ["[OH]"], "include": tokens}
    )
    assert captured[0]["body"]["include"] == tokens
    client.close()


# ---------------------------------------------------------------------------
# Pagination & collapse
# ---------------------------------------------------------------------------


def test_limit_capped_to_max() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(
        client, _cfg(), {"reactants": ["[OH]"], "limit": 10_000}
    )
    assert captured[0]["body"]["limit"] == 50  # DEFAULT_MAX_LIMIT
    client.close()


def test_default_limit_applied_when_omitted() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(client, _cfg(), {"reactants": ["[OH]"]})
    assert captured[0]["body"]["limit"] == 25  # DEFAULT_DEFAULT_LIMIT
    client.close()


def test_rejects_negative_offset() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        reactions_tool.run(client, _cfg(), {"reactants": ["[OH]"], "offset": -1})
    client.close()


@pytest.mark.parametrize("collapse", ["all", "first"])
def test_legal_collapse_values_forwarded(collapse: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    reactions_tool.run(
        client, _cfg(), {"reactants": ["[OH]"], "collapse": collapse}
    )
    assert captured[0]["body"]["collapse"] == collapse
    client.close()


def test_rejects_unknown_collapse_value() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        reactions_tool.run(
            client, _cfg(), {"reactants": ["[OH]"], "collapse": "every"}
        )
    client.close()


# ---------------------------------------------------------------------------
# Envelope passthrough + server error mapping
# ---------------------------------------------------------------------------


def test_envelope_returned_unchanged() -> None:
    fixture = _stub_response(
        records=[{"reaction_entry_ref": "rxe_aaa", "reaction_ref": "rxn_bbb"}]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _make_client(handler)
    result = reactions_tool.run(client, _cfg(), {"family": "H_Abstraction"})
    assert result == fixture
    client.close()


def test_server_422_maps_to_invalid_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": "post_search_fields_must_be_in_body",
                "code": "post_search_fields_must_be_in_body",
            },
        )

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(client, _cfg(), {"family": "H_Abstraction"})
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.http_status == 422
    client.close()


def test_server_404_maps_to_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "reaction_entry not found"})

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        reactions_tool.run(
            client, _cfg(), {"reaction_entry_ref": "rxe_missing"}
        )
    assert excinfo.value.code == "not_found"
    assert excinfo.value.http_status == 404
    client.close()


def test_no_api_key_header_when_unset() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json=_stub_response())

    client = _make_client(handler)
    reactions_tool.run(client, _cfg(), {"reactants": ["[OH]"]})
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
    reactions_tool.run(client, _cfg(), {"reactants": ["[OH]"]})
    assert seen[0].get("x-api-key") == "tck_xyz"
    client.close()
