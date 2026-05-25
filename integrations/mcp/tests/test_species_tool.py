"""Tests for ``tckdb_mcp.tools.species``."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.tools import species as species_tool


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
        params = request.url.params
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "body": json.loads(request.content.decode("utf-8") or "null"),
                "query": {
                    key: values[0] if len(values) == 1 else values
                    for key in params
                    if (values := params.get_list(key))
                },
                "headers": dict(request.headers),
            }
        )
        return httpx.Response(200, json=_stub_response())

    return handler


def _cfg() -> Config:
    return Config.from_env(env={})


# ---------------------------------------------------------------------------
# URL routing
# ---------------------------------------------------------------------------


def test_species_search_url_under_api_v1() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    species_tool.run(client, _cfg(), {"smiles": "CCO"})
    assert captured[0]["method"] == "GET"
    assert captured[0]["url"].startswith(
        "http://127.0.0.1:8010/api/v1/scientific/species/search"
    )
    client.close()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_rejects_when_no_identity_field() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, _cfg(), {})
    assert excinfo.value.code == "invalid_input"
    assert "identity field" in excinfo.value.detail
    client.close()


def test_rejects_when_only_metadata_supplied() -> None:
    """``charge`` alone is not a search identity."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        species_tool.run(client, _cfg(), {"charge": 0, "multiplicity": 2})
    client.close()


@pytest.mark.parametrize("field", ["species_id", "species_entry_id"])
def test_rejects_integer_id_fields(field: str) -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, _cfg(), {"smiles": "CCO", field: 42})
    assert excinfo.value.code == "invalid_input"
    assert "integer-id" in excinfo.value.detail
    client.close()


def test_rejects_unknown_field() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, _cfg(), {"smiles": "CCO", "frobnicate": True})
    assert excinfo.value.code == "invalid_input"
    assert "unknown field" in excinfo.value.detail
    client.close()


def test_rejects_internal_ids_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(
            client, _cfg(), {"smiles": "CCO", "include": ["internal_ids"]}
        )
    assert excinfo.value.code == "invalid_input"
    assert "internal_ids" in excinfo.value.detail
    client.close()


def test_rejects_unknown_include_token() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, _cfg(), {"smiles": "CCO", "include": ["bogus"]})
    assert excinfo.value.code == "invalid_input"
    assert "bogus" in excinfo.value.detail
    client.close()


def test_rejects_species_ref_with_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, _cfg(), {"species_ref": "spe_abc"})
    assert excinfo.value.code == "invalid_input"
    assert "spc_" in excinfo.value.detail
    client.close()


def test_rejects_species_entry_ref_with_wrong_prefix() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, _cfg(), {"species_entry_ref": "spc_abc"})
    assert excinfo.value.code == "invalid_input"
    assert "spe_" in excinfo.value.detail
    client.close()


def test_rejects_integer_shaped_ref() -> None:
    """Integer-looking refs (no prefix) must be rejected client-side."""
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        species_tool.run(client, _cfg(), {"species_ref": "42"})
    client.close()


def test_rejects_negative_offset() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        species_tool.run(client, _cfg(), {"smiles": "CCO", "offset": -1})
    client.close()


def test_rejects_non_positive_limit() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        species_tool.run(client, _cfg(), {"smiles": "CCO", "limit": 0})
    client.close()


def test_rejects_bad_collapse_value() -> None:
    client = _make_client(_ok_handler([]))
    with pytest.raises(MCPToolError):
        species_tool.run(client, _cfg(), {"smiles": "CCO", "collapse": "every"})
    client.close()


# ---------------------------------------------------------------------------
# Limit cap & forwarding
# ---------------------------------------------------------------------------


def test_limit_capped_to_max() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    species_tool.run(client, _cfg(), {"smiles": "CCO", "limit": 10_000})
    assert captured[0]["query"]["limit"] == "50"  # DEFAULT_MAX_LIMIT
    client.close()


def test_default_limit_applied_when_omitted() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    species_tool.run(client, _cfg(), {"smiles": "CCO"})
    assert captured[0]["query"]["limit"] == "25"  # DEFAULT_DEFAULT_LIMIT
    client.close()


def test_collapse_all_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    species_tool.run(client, _cfg(), {"smiles": "CCO", "collapse": "all"})
    assert captured[0]["query"]["collapse"] == "all"
    client.close()


def test_collapse_first_forwarded() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    species_tool.run(client, _cfg(), {"smiles": "CCO", "collapse": "first"})
    assert captured[0]["query"]["collapse"] == "first"
    client.close()


def test_valid_payload_forwarded_with_all_supplied_fields() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    species_tool.run(
        client,
        _cfg(),
        {
            "smiles": "CCO",
            "inchi_key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            "charge": 0,
            "multiplicity": 1,
            "include": ["thermo", "review"],
            "collapse": "first",
            "offset": 10,
            "limit": 20,
            "include_rejected": False,
            "include_deprecated": False,
        },
    )
    body = captured[0]["query"]
    assert body["smiles"] == "CCO"
    assert body["inchi_key"] == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
    assert body["charge"] == "0"
    assert body["multiplicity"] == "1"
    assert body["include"] == ["thermo", "review"]
    assert body["collapse"] == "first"
    assert body["offset"] == "10"
    assert body["limit"] == "20"
    assert body["include_rejected"] == "false"
    # ``None`` fields are omitted (cleaner payload, server-side friendlier).
    assert "min_review_status" not in body
    client.close()


def test_well_formed_refs_pass_validation() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    species_tool.run(client, _cfg(), {"species_entry_ref": "spe_01HXYZ"})
    assert captured[0]["query"]["species_entry_ref"] == "spe_01HXYZ"
    client.close()


def test_include_all_token_passes() -> None:
    captured: list[dict[str, Any]] = []
    client = _make_client(_ok_handler(captured))
    species_tool.run(client, _cfg(), {"smiles": "CCO", "include": ["all"]})
    assert captured[0]["query"]["include"] == "all"
    client.close()


def test_envelope_returned_unchanged() -> None:
    fixture = _stub_response(
        records=[{"species_entry_ref": "spe_aaa", "species_ref": "spc_bbb"}]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture)

    client = _make_client(handler)
    result = species_tool.run(client, _cfg(), {"smiles": "CCO"})
    assert result == fixture
    client.close()
