"""Tests for ``tckdb_mcp.tools.geometry``.

Path-handle edge cases (every unsafe character, oversized refs, etc.)
are covered directly in ``test_path_handles.py``. Tests here prove
this tool *wires* the helper with the right prefix and that the
geometry-specific surface (include vocab, default empty include, URL
under ``/scientific/geometries/...``) behaves correctly.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool, list_tools_payload
from tckdb_mcp.tools import geometry as geom_tool

TOOL_NAME = "tckdb_get_geometry"
VALID_REF = "geom_01HZ7AAA"


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
        "geometry_ref": VALID_REF,
        "symbols": ["C", "C", "O", "H", "H", "H", "H", "H", "H"],
        "coords": [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [2.0, 1.0, 0.0],
            [-0.5, 1.0, 0.0],
            [-0.5, -1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.8, -1.0, 0.0],
            [1.8, 0.5, 1.0],
            [2.8, 0.7, 0.0],
        ],
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


def test_dispatch_routes_to_geometry_tool() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    result = dispatch_tool(
        TOOL_NAME, {"geometry_ref": VALID_REF}, client, _cfg()
    )
    assert captured[0]["path"] == f"/api/v1/scientific/geometries/{VALID_REF}"
    assert "symbols" in result
    assert "coords" in result
    client.close()


# ---------------------------------------------------------------------------
# URL routing
# ---------------------------------------------------------------------------


def test_url_resolves_under_api_v1_with_ref_in_path() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    geom_tool.run(client, {"geometry_ref": VALID_REF})
    assert captured[0]["url"].startswith(
        f"http://127.0.0.1:8010/api/v1/scientific/geometries/{VALID_REF}"
    )
    assert captured[0]["method"] == "GET"
    client.close()


# ---------------------------------------------------------------------------
# Required ref / prefix validation (smoke tests; helper covers edge cases)
# ---------------------------------------------------------------------------


def test_rejects_missing_geometry_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(client, {})
    assert excinfo.value.code == "invalid_input"
    assert "geometry_ref" in excinfo.value.detail
    client.close()


def test_accepts_valid_geom_ref() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    geom_tool.run(client, {"geometry_ref": VALID_REF})
    assert captured[0]["path"].endswith(f"/{VALID_REF}")
    client.close()


@pytest.mark.parametrize(
    "ref",
    ["spe_01HZ", "rxe_01HZ", "rxn_01HZ", "spc_01HZ", "lot_01HZ"],
)
def test_rejects_wrong_prefix_refs(ref: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(client, {"geometry_ref": ref})
    assert excinfo.value.code == "invalid_input"
    assert "geom_" in excinfo.value.detail
    client.close()


def test_rejects_integer_shaped_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        geom_tool.run(client, {"geometry_ref": "42"})
    client.close()


def test_rejects_path_unsafe_ref_via_shared_helper() -> None:
    """Spot-check that the helper wiring fires; exhaustive coverage is in test_path_handles."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(client, {"geometry_ref": "geom_a/b"})
    assert "path-unsafe" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Integer-ID teaching error
# ---------------------------------------------------------------------------


def test_rejects_geometry_id_with_teaching_error() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(client, {"geometry_id": 7})
    assert excinfo.value.code == "invalid_input"
    assert "integer-id" in excinfo.value.detail
    assert "geometry_ref" in excinfo.value.detail
    client.close()


def test_integer_id_rejection_runs_before_unknown_field_check() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(client, {"geometry_id": 1, "frobnicate": True})
    assert "integer-id" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Unknown fields
# ---------------------------------------------------------------------------


def test_rejects_unknown_field() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(
            client, {"geometry_ref": VALID_REF, "frobnicate": True}
        )
    assert "unknown field" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "field",
    [
        "temperature_min",
        "temperature_max",
        "pressure",
        "limit",
        "offset",
        "collapse",
        "model_kind",
        "level_of_theory_ref",
        "software",
        "min_review_status",
    ],
)
def test_rejects_search_or_filter_fields_not_supported_by_endpoint(field: str) -> None:
    """Geometry endpoint has no filters; every search-style field must be rejected."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(client, {"geometry_ref": VALID_REF, field: "x"})
    assert "unknown field" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Include tokens
# ---------------------------------------------------------------------------


def test_default_include_is_empty() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    geom_tool.run(client, {"geometry_ref": VALID_REF})
    sent = captured[0]["url"]
    assert "include=" not in sent
    client.close()


def test_explicit_empty_include_sends_nothing() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    geom_tool.run(client, {"geometry_ref": VALID_REF, "include": []})
    assert "include=" not in captured[0]["url"]
    client.close()


def test_rejects_internal_ids_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(
            client,
            {"geometry_ref": VALID_REF, "include": ["internal_ids"]},
        )
    assert "internal_ids" in excinfo.value.detail
    client.close()


def test_rejects_unknown_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(
            client, {"geometry_ref": VALID_REF, "include": ["bogus"]}
        )
    assert "bogus" in excinfo.value.detail
    client.close()


def test_rejects_tokens_from_other_endpoints() -> None:
    """``calculations`` is legal elsewhere but not for geometry detail."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(
            client,
            {"geometry_ref": VALID_REF, "include": ["calculations"]},
        )
    assert "calculations" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize("token", ["review", "provenance", "all"])
def test_each_legal_include_token_accepted(token: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    geom_tool.run(client, {"geometry_ref": VALID_REF, "include": [token]})
    sent = [v for k, v in captured[0]["multi_params"] if k == "include"]
    assert sent == [token]
    client.close()


def test_multiple_legal_tokens_forwarded_as_repeated_params() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    geom_tool.run(
        client,
        {"geometry_ref": VALID_REF, "include": ["review", "provenance"]},
    )
    sent = [v for k, v in captured[0]["multi_params"] if k == "include"]
    assert sent == ["review", "provenance"]
    client.close()


def test_rejects_non_list_include() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        geom_tool.run(
            client, {"geometry_ref": VALID_REF, "include": "provenance"}
        )
    client.close()


# ---------------------------------------------------------------------------
# Error envelope mapping
# ---------------------------------------------------------------------------


def test_server_422_maps_to_invalid_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": "handle_type_mismatch: expected geom_*",
                "code": "handle_type_mismatch",
            },
        )

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(client, {"geometry_ref": VALID_REF})
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.http_status == 422
    client.close()


def test_server_404_maps_to_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "geometry not found"})

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        geom_tool.run(client, {"geometry_ref": "geom_missing"})
    assert excinfo.value.code == "not_found"
    client.close()


def test_response_passed_through_unchanged() -> None:
    fixture = _stub_response()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _make_client(handler)
    result = geom_tool.run(client, {"geometry_ref": VALID_REF})
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
    geom_tool.run(client, {"geometry_ref": VALID_REF})
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
    geom_tool.run(client, {"geometry_ref": VALID_REF})
    assert seen[0].get("x-api-key") == "tck_xyz"
    client.close()
