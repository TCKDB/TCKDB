"""The statmech MCP tools forward every filter and include the backend accepts, and expand nothing."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool


def _run(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "path": request.url.path,
                "query": request.url.params.multi_items(),
                "body": json.loads(request.content.decode() or "null"),
            }
        )
        return httpx.Response(200, json={"records": []})

    client = TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1", api_key=None, timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )
    dispatch_tool(tool, args, client, Config.from_env(env={}))
    assert len(seen) == 1
    return seen[0]


@pytest.mark.parametrize(
    "args",
    [
        {"has_torsions": False},
        {"has_freq_calculation": True},
        {"has_rotor_scans": False},
        {"has_source_calculations": False},
        {"workflow_tool": "ARC"},
        {"workflow_tool_version": "1.1.0"},
        {"software_version": "16"},
        {"transition_state_entry_ref": "tse_abc"},
        {"declared_conformer_group_ref": "cg_abc"},
    ],
)
def test_every_backend_discriminator_is_accepted_alone(args: dict[str, Any]) -> None:
    sent = _run("tckdb_statmech_search", args)
    for key, value in args.items():
        assert sent["body"][key] == value


def test_explicit_false_is_forwarded_as_false() -> None:
    body = _run("tckdb_statmech_search", {"has_torsions": False})["body"]
    assert body["has_torsions"] is False


def test_no_discriminator_is_still_refused() -> None:
    with pytest.raises(MCPToolError):
        _run("tckdb_statmech_search", {"include": ["review"]})


def test_transition_state_entry_id_is_refused() -> None:
    with pytest.raises(MCPToolError, match="integer-id"):
        _run("tckdb_statmech_search", {"transition_state_entry_id": 3, "statmech_ref": "sm_a"})


@pytest.mark.parametrize("token", ["electronic_levels", "trust"])
def test_search_accepts_new_include_tokens(token: str) -> None:
    assert _run("tckdb_statmech_search", {"statmech_ref": "sm_a", "include": [token]})["body"]["include"] == [token]


@pytest.mark.parametrize("token", ["electronic_levels", "trust"])
def test_detail_forwards_new_include_tokens(token: str) -> None:
    sent = _run("tckdb_statmech_detail", {"statmech_ref": "sm_a", "include": [token]})
    assert ("include", token) in sent["query"]


def test_all_is_never_expanded() -> None:
    assert _run("tckdb_statmech_search", {"statmech_ref": "sm_a", "include": ["all"]})["body"]["include"] == ["all"]
    detail = _run("tckdb_statmech_detail", {"statmech_ref": "sm_a", "include": ["all"]})
    assert [v for k, v in detail["query"] if k == "include"] == ["all"]


@pytest.mark.parametrize("token", ["internal_ids", "assessments"])
def test_internal_tokens_stay_refused(token: str) -> None:
    for tool, args in (
        ("tckdb_statmech_search", {"statmech_ref": "sm_a"}),
        ("tckdb_statmech_detail", {"statmech_ref": "sm_a"}),
    ):
        with pytest.raises(MCPToolError):
            _run(tool, {**args, "include": [token]})
