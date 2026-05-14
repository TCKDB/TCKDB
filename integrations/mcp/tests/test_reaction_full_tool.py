"""Tests for ``tckdb_mcp.tools.reaction_full``.

Path-handle edge cases live in ``test_path_handles.py``. Tests here
prove this tool wires the helper with ``rxe_`` and validate the
composite-read-specific surface (default include, include_review enum,
no pagination/temperature/etc.).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool, list_tools_payload
from tckdb_mcp.tools import reaction_full as rf_tool

TOOL_NAME = "tckdb_get_reaction_entry_full"
VALID_REF = "rxe_01HZY3K9X2"


def _make_client(handler) -> TCKDBHttpClient:
    transport = httpx.MockTransport(handler)
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1",
        api_key=None,
        timeout_seconds=5.0,
        transport=transport,
    )


def _stub_response() -> dict[str, Any]:
    return {
        "reaction_entry_ref": VALID_REF,
        "reaction_ref": "rxn_01HZY3",
        "species": [],
        "kinetics": [],
        "transition_states": [],
        "review_summary": {
            "approved": 0,
            "under_review": 0,
            "rejected": 0,
            "total": 0,
        },
    }


def _ok_handler(captured: list[dict[str, Any]]):
    def handler(request: httpx.Request) -> httpx.Response:
        multi = list(request.url.params.multi_items())
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "params": dict(multi),
                "multi_params": multi,
                "headers": dict(request.headers),
            }
        )
        return httpx.Response(200, json=_stub_response())

    return handler


def _cfg() -> Config:
    return Config.from_env(env={})


# ---------------------------------------------------------------------------
# Registration / dispatch
# ---------------------------------------------------------------------------


def test_tool_registered_in_list_tools_payload() -> None:
    names = [entry["name"] for entry in list_tools_payload()]
    assert TOOL_NAME in names


def test_dispatch_routes_to_reaction_full_tool() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    result = dispatch_tool(
        TOOL_NAME, {"reaction_entry_ref": VALID_REF}, client, _cfg()
    )
    assert captured[0]["path"] == (
        f"/api/v1/scientific/reaction-entries/{VALID_REF}/full"
    )
    assert "reaction_entry_ref" in result
    client.close()


# ---------------------------------------------------------------------------
# URL routing
# ---------------------------------------------------------------------------


def test_url_resolves_under_api_v1_with_ref_in_path() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rf_tool.run(client, {"reaction_entry_ref": VALID_REF})
    assert captured[0]["url"].startswith(
        f"http://127.0.0.1:8010/api/v1/scientific/reaction-entries/{VALID_REF}/full"
    )
    assert captured[0]["method"] == "GET"
    client.close()


# ---------------------------------------------------------------------------
# Required ref / prefix validation (smoke tests; helper covers edge cases)
# ---------------------------------------------------------------------------


def test_rejects_missing_reaction_entry_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(client, {})
    assert excinfo.value.code == "invalid_input"
    assert "reaction_entry_ref" in excinfo.value.detail
    client.close()


def test_accepts_valid_rxe_ref() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rf_tool.run(client, {"reaction_entry_ref": VALID_REF})
    assert captured[0]["path"].endswith(f"/{VALID_REF}/full")
    client.close()


@pytest.mark.parametrize(
    "ref",
    ["rxn_01HZY", "spe_01HZ", "spc_01HZ", "geom_01HZ", "lot_01HZ"],
)
def test_rejects_wrong_prefix_refs(ref: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(client, {"reaction_entry_ref": ref})
    assert excinfo.value.code == "invalid_input"
    assert "rxe_" in excinfo.value.detail
    client.close()


def test_rejects_integer_shaped_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        rf_tool.run(client, {"reaction_entry_ref": "42"})
    client.close()


def test_rejects_path_unsafe_ref_via_shared_helper() -> None:
    """Spot-check helper wiring; exhaustive coverage in test_path_handles."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(client, {"reaction_entry_ref": "rxe_a/b"})
    assert "path-unsafe" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Integer-ID teaching errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["reaction_entry_id", "reaction_id", "species_id", "species_entry_id"],
)
def test_rejects_integer_id_fields_with_teaching_error(field: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(client, {"reaction_entry_ref": VALID_REF, field: 7})
    assert excinfo.value.code == "invalid_input"
    assert "integer-id" in excinfo.value.detail
    assert "reaction_entry_ref" in excinfo.value.detail
    client.close()


def test_integer_id_rejection_runs_before_unknown_field_check() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(client, {"reaction_entry_id": 1, "frobnicate": True})
    assert "integer-id" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Unknown / unsupported fields
# ---------------------------------------------------------------------------


def test_rejects_unknown_field() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(
            client, {"reaction_entry_ref": VALID_REF, "frobnicate": True}
        )
    assert "unknown field" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "field",
    [
        "temperature_min",
        "temperature_max",
        "pressure",
        "model_kind",
        "level_of_theory_ref",
        "software",
        "limit",
        "offset",
        "collapse",
        "sort",
    ],
)
def test_rejects_pagination_filter_or_search_fields(field: str) -> None:
    """The composite endpoint has no pagination, filters, or sort."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(client, {"reaction_entry_ref": VALID_REF, field: "x"})
    assert "unknown field" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Include tokens
# ---------------------------------------------------------------------------


def test_default_include_is_species_kinetics_transition_states() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rf_tool.run(client, {"reaction_entry_ref": VALID_REF})
    sent = [v for k, v in captured[0]["multi_params"] if k == "include"]
    assert sent == ["species", "kinetics", "transition_states"]
    client.close()


def test_explicit_empty_include_overrides_default() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rf_tool.run(client, {"reaction_entry_ref": VALID_REF, "include": []})
    assert "include=" not in captured[0]["url"]
    client.close()


def test_rejects_internal_ids_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(
            client,
            {"reaction_entry_ref": VALID_REF, "include": ["internal_ids"]},
        )
    assert "internal_ids" in excinfo.value.detail
    client.close()


def test_rejects_unknown_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(
            client, {"reaction_entry_ref": VALID_REF, "include": ["bogus"]}
        )
    assert "bogus" in excinfo.value.detail
    client.close()


def test_rejects_thermo_token_not_in_legal_set() -> None:
    """``thermo`` was suggested in the prompt but is not in the route's vocab."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(
            client, {"reaction_entry_ref": VALID_REF, "include": ["thermo"]}
        )
    assert "thermo" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "token",
    [
        "species",
        "kinetics",
        "transition_states",
        "calculations",
        "path_search",
        "irc",
        "scans",
        "conformers",
        "artifacts",
        "review",
        "all",
    ],
)
def test_each_legal_include_token_accepted(token: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rf_tool.run(
        client,
        {"reaction_entry_ref": VALID_REF, "include": [token]},
    )
    sent = [v for k, v in captured[0]["multi_params"] if k == "include"]
    assert sent == [token]
    client.close()


def test_full_include_set_forwarded() -> None:
    """Bulk happy-path: every legal token sent at once."""
    full_set = sorted(rf_tool.LEGAL_INCLUDE_TOKENS - {"all"})
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rf_tool.run(
        client, {"reaction_entry_ref": VALID_REF, "include": full_set}
    )
    sent = [v for k, v in captured[0]["multi_params"] if k == "include"]
    assert sent == full_set
    client.close()


def test_rejects_non_list_include() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        rf_tool.run(
            client, {"reaction_entry_ref": VALID_REF, "include": "species"}
        )
    client.close()


# ---------------------------------------------------------------------------
# include_review enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["summary", "full"])
def test_include_review_legal_values_forwarded(value: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rf_tool.run(
        client,
        {"reaction_entry_ref": VALID_REF, "include_review": value},
    )
    assert captured[0]["params"]["include_review"] == value
    client.close()


def test_rejects_unknown_include_review_value() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(
            client,
            {"reaction_entry_ref": VALID_REF, "include_review": "none"},
        )
    assert "include_review" in excinfo.value.detail
    client.close()


def test_include_review_omitted_when_unset() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rf_tool.run(client, {"reaction_entry_ref": VALID_REF})
    assert "include_review=" not in captured[0]["url"]
    client.close()


# ---------------------------------------------------------------------------
# Review filters
# ---------------------------------------------------------------------------


def test_review_filters_serialize_as_lowercase_strings() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rf_tool.run(
        client,
        {
            "reaction_entry_ref": VALID_REF,
            "min_review_status": "approved",
            "include_rejected": False,
            "include_deprecated": True,
        },
    )
    p = captured[0]["params"]
    assert p["min_review_status"] == "approved"
    assert p["include_rejected"] == "false"
    assert p["include_deprecated"] == "true"
    client.close()


# ---------------------------------------------------------------------------
# Error envelope mapping
# ---------------------------------------------------------------------------


def test_server_422_maps_to_invalid_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": "handle_type_mismatch: expected rxe_*",
                "code": "handle_type_mismatch",
            },
        )

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(client, {"reaction_entry_ref": VALID_REF})
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.http_status == 422
    client.close()


def test_server_404_maps_to_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "reaction_entry not found"})

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        rf_tool.run(client, {"reaction_entry_ref": "rxe_missing"})
    assert excinfo.value.code == "not_found"
    client.close()


def test_response_passed_through_unchanged() -> None:
    fixture = _stub_response()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _make_client(handler)
    result = rf_tool.run(client, {"reaction_entry_ref": VALID_REF})
    assert result == fixture
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
    rf_tool.run(client, {"reaction_entry_ref": VALID_REF})
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
    rf_tool.run(client, {"reaction_entry_ref": VALID_REF})
    assert seen[0].get("x-api-key") == "tck_xyz"
    client.close()
