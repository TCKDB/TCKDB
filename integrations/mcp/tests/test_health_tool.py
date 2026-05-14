"""Tests for ``tckdb_mcp.tools.health`` and the health URL routing rule."""

from __future__ import annotations

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.tools import health as health_tool


def _client_with(handler, base_url: str = "http://127.0.0.1:8010/api/v1") -> TCKDBHttpClient:
    transport = httpx.MockTransport(handler)
    return TCKDBHttpClient(
        base_url=base_url, api_key=None, timeout_seconds=5.0, transport=transport
    )


def test_health_url_strips_api_v1_prefix() -> None:
    client = _client_with(lambda r: httpx.Response(200, json={"status": "ok"}))
    assert client.health_url() == "http://127.0.0.1:8010/health"
    client.close()


def test_health_url_unchanged_when_no_api_prefix() -> None:
    client = _client_with(
        lambda r: httpx.Response(200, json={"status": "ok"}),
        base_url="http://example.com",
    )
    assert client.health_url() == "http://example.com/health"
    client.close()


def test_health_url_strips_api_v2() -> None:
    client = _client_with(
        lambda r: httpx.Response(200, json={"status": "ok"}),
        base_url="http://example.com/api/v2",
    )
    assert client.health_url() == "http://example.com/health"
    client.close()


def test_health_returns_ok_on_2xx() -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        assert request.method == "GET"
        return httpx.Response(200, json={"status": "ok"})

    client = _client_with(handler)
    result = health_tool.run(client)
    assert result == {"status": "ok"}
    assert seen_urls == ["http://127.0.0.1:8010/health"]
    client.close()


def test_health_normalizes_response_with_extra_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0"})

    client = _client_with(handler)
    result = health_tool.run(client)
    assert result == {"status": "ok", "version": "0.1.0"}
    client.close()


def test_health_normalizes_response_without_status_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ready": True})

    client = _client_with(handler)
    result = health_tool.run(client)
    assert result["status"] == "ok"
    assert result["ready"] is True
    client.close()


def test_health_rejects_arguments() -> None:
    client = _client_with(lambda r: httpx.Response(200, json={}))
    with pytest.raises(MCPToolError) as excinfo:
        health_tool.run(client, {"unexpected": "arg"})
    assert excinfo.value.code == "invalid_input"
    client.close()


def test_health_accepts_empty_arguments() -> None:
    client = _client_with(lambda r: httpx.Response(200, json={"status": "ok"}))
    assert health_tool.run(client, {}) == {"status": "ok"}
    client.close()


def test_health_forwards_api_key_when_set() -> None:
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    client = TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1",
        api_key="tck_xyz",
        timeout_seconds=5.0,
        transport=transport,
    )
    health_tool.run(client)
    assert seen_headers[0].get("x-api-key") == "tck_xyz"
    client.close()


def test_health_omits_api_key_when_unset() -> None:
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        return httpx.Response(200, json={"status": "ok"})

    client = _client_with(handler)
    health_tool.run(client)
    assert "x-api-key" not in {k.lower() for k in seen_headers[0]}
    client.close()


def test_config_from_env_consumed_correctly() -> None:
    """Smoke test: the config + client + tool wire together cleanly."""
    cfg = Config.from_env(env={"TCKDB_BASE_URL": "http://127.0.0.1:8010/api/v1"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:8010/health"
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    client = TCKDBHttpClient(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        timeout_seconds=cfg.timeout_seconds,
        transport=transport,
    )
    assert health_tool.run(client) == {"status": "ok"}
    client.close()
