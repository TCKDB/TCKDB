"""Tests for HTTP status / transport-exception → MCP error mapping."""

from __future__ import annotations

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import (
    MCPToolError,
    invalid_input,
    map_http_status,
    map_httpx_exception,
)
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.tools import species as species_tool


def _client(handler) -> TCKDBHttpClient:
    transport = httpx.MockTransport(handler)
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1",
        api_key=None,
        timeout_seconds=2.0,
        transport=transport,
    )


# ---------------------------------------------------------------------------
# Pure mapping tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_input"),
        (401, "auth_required"),
        (403, "forbidden"),
        (404, "not_found"),
        (409, "conflict"),
        (422, "invalid_input"),
        (429, "rate_limited"),
        (500, "service_unavailable"),
        (502, "service_unavailable"),
        (503, "service_unavailable"),
        (504, "service_unavailable"),
    ],
)
def test_map_http_status_table(status: int, code: str) -> None:
    err = map_http_status(status, "some detail")
    assert err.code == code
    assert err.http_status == status
    assert err.detail == "some detail"


def test_map_http_status_unknown_falls_through_to_internal_error() -> None:
    err = map_http_status(418, "i'm a teapot")
    assert err.code == "internal_error"
    assert err.http_status == 418


def test_map_http_status_blank_detail_falls_back_to_status_string() -> None:
    err = map_http_status(503, "")
    assert err.detail == "HTTP 503"


def test_invalid_input_helper() -> None:
    err = invalid_input("bad include token: foo")
    assert err.code == "invalid_input"
    assert err.http_status == 422
    assert "foo" in err.detail


def test_map_httpx_timeout_exception() -> None:
    err = map_httpx_exception(httpx.ReadTimeout("read timeout"))
    assert err.code == "timeout"
    assert err.http_status is None


def test_map_httpx_transport_error() -> None:
    err = map_httpx_exception(httpx.ConnectError("conn refused"))
    assert err.code == "network_error"
    assert err.http_status is None


def test_map_httpx_unexpected_exception() -> None:
    err = map_httpx_exception(RuntimeError("oops"))
    assert err.code == "internal_error"


def test_to_payload_shape() -> None:
    err = MCPToolError("not_found", "no such ref", http_status=404)
    assert err.to_payload() == {
        "code": "not_found",
        "detail": "no such ref",
        "http_status": 404,
    }


def test_detail_scrubs_large_integers() -> None:
    """Defensive: any large bare integer in detail is masked."""
    err = map_http_status(422, "violates constraint on row 1234567")
    assert "1234567" not in err.detail
    assert "<id>" in err.detail


# ---------------------------------------------------------------------------
# End-to-end mapping through the HTTP wrapper
# ---------------------------------------------------------------------------


def test_http_422_from_server_maps_to_invalid_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": "unknown_include_token: 'foo' not legal for species_search",
                "code": "unknown_include_token",
            },
        )

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "invalid_input"
    assert excinfo.value.http_status == 422
    assert "unknown_include_token" in excinfo.value.detail
    client.close()


def test_http_404_from_server_maps_to_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "species_entry not found"})

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(
            client,
            Config.from_env(env={}),
            {"species_entry_ref": "spe_doesnotexist"},
        )
    assert excinfo.value.code == "not_found"
    assert excinfo.value.http_status == 404
    client.close()


def test_http_503_from_server_maps_to_service_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "query timeout"})

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "service_unavailable"
    client.close()


def test_timeout_maps_to_timeout_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout")

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "timeout"
    assert excinfo.value.http_status is None
    client.close()


def test_network_failure_maps_to_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection refused")

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "network_error"
    assert excinfo.value.http_status is None
    client.close()


def test_html_response_body_falls_back_to_status_detail() -> None:
    """Non-JSON 500 responses (e.g. proxy HTML) still produce a clean envelope."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>boom</html>")

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "service_unavailable"
    assert excinfo.value.http_status == 500
    client.close()
