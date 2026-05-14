"""Tests for ``tckdb_mcp.tools.reaction_kinetics``."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool, list_tools_payload
from tckdb_mcp.tools import reaction_kinetics as rk_tool

TOOL_NAME = "tckdb_get_reaction_entry_kinetics"
VALID_REF = "rxe_01HZY3K9X2"


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
            "offset": 0,
            "limit": 25,
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
        multi = list(request.url.params.multi_items())
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "params": dict(multi),  # last-write-wins; OK for single-value keys
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


def test_dispatch_routes_to_reaction_kinetics_tool() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    result = dispatch_tool(
        TOOL_NAME, {"reaction_entry_ref": VALID_REF}, client, _cfg()
    )
    assert captured[0]["path"] == (
        f"/api/v1/scientific/reaction-entries/{VALID_REF}/kinetics"
    )
    assert "records" in result
    client.close()


# ---------------------------------------------------------------------------
# URL routing
# ---------------------------------------------------------------------------


def test_url_resolves_under_api_v1_with_ref_in_path() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(client, _cfg(), {"reaction_entry_ref": VALID_REF})
    # url without query string:
    assert captured[0]["url"].startswith(
        f"http://127.0.0.1:8010/api/v1/scientific/reaction-entries/{VALID_REF}/kinetics"
    )
    assert captured[0]["method"] == "GET"
    client.close()


# ---------------------------------------------------------------------------
# Required ref / prefix validation
# ---------------------------------------------------------------------------


def test_rejects_missing_reaction_entry_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(client, _cfg(), {})
    assert excinfo.value.code == "invalid_input"
    assert "reaction_entry_ref" in excinfo.value.detail
    client.close()


def test_rejects_empty_string_reaction_entry_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        rk_tool.run(client, _cfg(), {"reaction_entry_ref": ""})
    client.close()


def test_rejects_non_string_reaction_entry_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(client, _cfg(), {"reaction_entry_ref": 42})
    assert "must be a string" in excinfo.value.detail
    client.close()


def test_accepts_valid_rxe_ref() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(client, _cfg(), {"reaction_entry_ref": VALID_REF})
    assert captured[0]["path"].endswith(f"/{VALID_REF}/kinetics")
    client.close()


@pytest.mark.parametrize(
    "ref",
    ["rxn_01HZY3K9X2", "spe_01HZY3K9X2", "spc_01HZY3K9X2", "geom_01HZ"],
)
def test_rejects_wrong_prefix_refs(ref: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(client, _cfg(), {"reaction_entry_ref": ref})
    assert excinfo.value.code == "invalid_input"
    assert "rxe_" in excinfo.value.detail
    client.close()


def test_rejects_integer_shaped_ref() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        rk_tool.run(client, _cfg(), {"reaction_entry_ref": "42"})
    client.close()


def test_rejects_bare_prefix_with_no_body() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(client, _cfg(), {"reaction_entry_ref": "rxe_"})
    assert "no body" in excinfo.value.detail
    client.close()


def test_rejects_ref_longer_than_64_chars() -> None:
    long_ref = "rxe_" + ("A" * 61)
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(client, _cfg(), {"reaction_entry_ref": long_ref})
    assert "64" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Path-safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_char",
    ["/", "?", "#", "&", " ", "\t", "\n"],
)
def test_rejects_path_unsafe_chars(bad_char: str) -> None:
    client = _make_client(_ok_handler([]))
    ref = f"rxe_abc{bad_char}def"
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(client, _cfg(), {"reaction_entry_ref": ref})
    assert excinfo.value.code == "invalid_input"
    assert "path-unsafe" in excinfo.value.detail
    client.close()


def test_path_traversal_attempt_rejected() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(
            client, _cfg(), {"reaction_entry_ref": "rxe_../../admin"}
        )
    assert excinfo.value.code == "invalid_input"
    assert "path-unsafe" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Integer-ID teaching errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["reaction_entry_id", "reaction_id", "level_of_theory_id"]
)
def test_rejects_integer_id_fields_with_teaching_error(field: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(
            client, _cfg(), {"reaction_entry_ref": VALID_REF, field: 7}
        )
    assert excinfo.value.code == "invalid_input"
    assert "integer-id" in excinfo.value.detail
    assert "reaction_entry_ref" in excinfo.value.detail
    client.close()


def test_integer_id_rejection_runs_before_unknown_field_check() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(
            client,
            _cfg(),
            {"reaction_entry_id": 1, "frobnicate": True},
        )
    assert "integer-id" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# level_of_theory_ref
# ---------------------------------------------------------------------------


def test_valid_level_of_theory_ref_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client,
        _cfg(),
        {"reaction_entry_ref": VALID_REF, "level_of_theory_ref": "lot_abc"},
    )
    assert captured[0]["params"]["level_of_theory_ref"] == "lot_abc"
    client.close()


def test_rejects_level_of_theory_ref_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(
            client,
            _cfg(),
            {"reaction_entry_ref": VALID_REF, "level_of_theory_ref": "rxe_abc"},
        )
    assert "lot_" in excinfo.value.detail
    client.close()


# ---------------------------------------------------------------------------
# Include tokens
# ---------------------------------------------------------------------------


def test_default_include_is_provenance() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(client, _cfg(), {"reaction_entry_ref": VALID_REF})
    includes = [v for k, v in captured[0]["multi_params"] if k == "include"]
    assert includes == ["provenance"]
    client.close()


def test_explicit_empty_include_overrides_default() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client, _cfg(), {"reaction_entry_ref": VALID_REF, "include": []}
    )
    sent = captured[0]["url"]
    assert "include=" not in sent  # empty list dropped from query string
    client.close()


def test_rejects_internal_ids_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(
            client,
            _cfg(),
            {"reaction_entry_ref": VALID_REF, "include": ["internal_ids"]},
        )
    assert "internal_ids" in excinfo.value.detail
    client.close()


def test_rejects_unknown_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(
            client,
            _cfg(),
            {"reaction_entry_ref": VALID_REF, "include": ["bogus"]},
        )
    assert "bogus" in excinfo.value.detail
    client.close()


@pytest.mark.parametrize(
    "token",
    [
        "provenance",
        "calculations",
        "transition_states",
        "path_search",
        "irc",
        "review",
        "artifacts",
        "all",
    ],
)
def test_each_legal_include_token_accepted(token: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client,
        _cfg(),
        {"reaction_entry_ref": VALID_REF, "include": [token]},
    )
    sent_tokens = [v for k, v in captured[0]["multi_params"] if k == "include"]
    assert sent_tokens == [token]
    client.close()


def test_multiple_legal_include_tokens_forwarded_as_repeated_params() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client,
        _cfg(),
        {
            "reaction_entry_ref": VALID_REF,
            "include": ["provenance", "calculations", "transition_states"],
        },
    )
    sent_tokens = [v for k, v in captured[0]["multi_params"] if k == "include"]
    assert sent_tokens == ["provenance", "calculations", "transition_states"]
    client.close()


# ---------------------------------------------------------------------------
# Pagination, collapse, filters
# ---------------------------------------------------------------------------


def test_limit_capped_to_max() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client,
        _cfg(),
        {"reaction_entry_ref": VALID_REF, "limit": 10_000},
    )
    assert captured[0]["params"]["limit"] == "50"  # DEFAULT_MAX_LIMIT
    client.close()


def test_default_limit_applied_when_omitted() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(client, _cfg(), {"reaction_entry_ref": VALID_REF})
    assert captured[0]["params"]["limit"] == "25"  # DEFAULT_DEFAULT_LIMIT
    client.close()


def test_offset_validation_negative_rejected() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        rk_tool.run(
            client,
            _cfg(),
            {"reaction_entry_ref": VALID_REF, "offset": -1},
        )
    client.close()


def test_offset_zero_accepted() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client, _cfg(), {"reaction_entry_ref": VALID_REF, "offset": 0}
    )
    assert captured[0]["params"]["offset"] == "0"
    client.close()


@pytest.mark.parametrize("collapse", ["all", "first"])
def test_legal_collapse_values_forwarded(collapse: str) -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client,
        _cfg(),
        {"reaction_entry_ref": VALID_REF, "collapse": collapse},
    )
    assert captured[0]["params"]["collapse"] == collapse
    client.close()


def test_rejects_unknown_collapse_value() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        rk_tool.run(
            client,
            _cfg(),
            {"reaction_entry_ref": VALID_REF, "collapse": "every"},
        )
    client.close()


def test_rejects_unknown_field() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(
            client,
            _cfg(),
            {"reaction_entry_ref": VALID_REF, "frobnicate": True},
        )
    assert "unknown field" in excinfo.value.detail
    client.close()


def test_temperature_and_pressure_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client,
        _cfg(),
        {
            "reaction_entry_ref": VALID_REF,
            "temperature_min": 300.0,
            "temperature_max": 2000.0,
            "pressure": 1.0,
        },
    )
    p = captured[0]["params"]
    assert p["temperature_min"] == "300.0"
    assert p["temperature_max"] == "2000.0"
    assert p["pressure"] == "1.0"
    client.close()


def test_rejects_non_numeric_temperature() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        rk_tool.run(
            client,
            _cfg(),
            {"reaction_entry_ref": VALID_REF, "temperature_min": "hot"},
        )
    client.close()


def test_software_filter_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client,
        _cfg(),
        {"reaction_entry_ref": VALID_REF, "software": "gaussian"},
    )
    assert captured[0]["params"]["software"] == "gaussian"
    client.close()


def test_review_filters_forwarded_as_lowercase_strings() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    rk_tool.run(
        client,
        _cfg(),
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
        rk_tool.run(client, _cfg(), {"reaction_entry_ref": VALID_REF})
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.http_status == 422
    client.close()


def test_server_404_maps_to_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "reaction_entry not found"})

    client = _make_client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        rk_tool.run(client, _cfg(), {"reaction_entry_ref": "rxe_missing"})
    assert excinfo.value.code == "not_found"
    client.close()


def test_envelope_returned_unchanged() -> None:
    fixture = _stub_response(
        records=[
            {"reaction_entry_ref": "rxe_aaa", "model_kind": "arrhenius"}
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _make_client(handler)
    result = rk_tool.run(client, _cfg(), {"reaction_entry_ref": VALID_REF})
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
    rk_tool.run(client, _cfg(), {"reaction_entry_ref": VALID_REF})
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
    rk_tool.run(client, _cfg(), {"reaction_entry_ref": VALID_REF})
    assert seen[0].get("x-api-key") == "tck_xyz"
    client.close()
