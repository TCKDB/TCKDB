"""Tests for ``tckdb_mcp.tools.network_kinetics_export`` (``tckdb_export_selected_network_kinetics``).

Request construction and verbatim pass-through only; agreement with the live server and with the Python client is
covered by ``backend/tests/api/scientific/test_network_select_parity.py``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool, list_tools_payload

TOOL_NAME = "tckdb_export_selected_network_kinetics"
REF = "net_01HZ5K9X2A"
MANIFEST = {"manifest_format_version": 1, "digest": {"algorithm": "sha256", "value": "0" * 64}}
CHOICE: dict[str, Any] = {"manifest": MANIFEST, "node_ref": "nkdet_x", "representation_refs": ["nkin_a", "nkin_b"]}


def _client(handler) -> TCKDBHttpClient:
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1", api_key=None, timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )


def _recording(answer: dict[str, Any] | None = None, status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer if answer is not None else {"format": "native"})

    return seen, handler


def _call(client, arguments: dict[str, Any]):
    return dispatch_tool(TOOL_NAME, arguments, client, Config.from_env(env={}))


def _args(**changes: Any) -> dict[str, Any]:
    return {"network_ref": REF, **CHOICE, **changes}


def test_tool_is_registered_with_a_closed_schema_and_no_id_or_rule_argument():
    (entry,) = [t for t in list_tools_payload() if t["name"] == TOOL_NAME]
    schema = entry["inputSchema"]
    assert schema["required"] == ["network_ref", "manifest", "node_ref", "representation_refs"]
    assert schema["additionalProperties"] is False
    assert not [k for k in schema["properties"] if k.endswith("_id") or "rule" in k]


def test_posts_to_the_export_path_with_only_what_was_supplied():
    seen, handler = _recording()
    _call(_client(handler), _args())
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path.endswith(f"/scientific/networks/{REF}/kinetics/export-selected")
    body = json.loads(request.content)
    assert body == CHOICE
    assert "allow_administrative_choice" not in body  # the server's default (false) stands


def test_forwards_every_optional_field_and_the_profile_and_an_explicit_false():
    seen, handler = _recording()
    extra = {"format": "chemkin", "allow_administrative_choice": True, "energy_units": "kj/mol", "naming_policy": "public_ref"}
    _call(_client(handler), _args(**extra, profile="curated"))
    _call(_client(handler), _args(allow_administrative_choice=False))
    assert json.loads(seen[0].content) == {**CHOICE, **extra}
    assert seen[0].url.params["profile"] == "curated"
    assert json.loads(seen[1].content)["allow_administrative_choice"] is False


def test_the_manifest_is_forwarded_exactly_as_given():
    seen, handler = _recording()
    manifest = {"a": [1, 2, {"b": None}], "digest": {"value": "x"}}
    _call(_client(handler), _args(manifest=manifest))
    assert json.loads(seen[0].content)["manifest"] == manifest


def test_the_servers_answer_and_refusal_are_returned_verbatim():
    answer = {"format": "native", "members": [], "files": None}
    _, handler = _recording(answer)
    assert _call(_client(handler), _args()) == answer
    refusal = {"code": "network_export_manifest_stale", "detail": "d", "context": {"differs": ["solves"]}}
    _, handler = _recording(refusal, status=422)
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), _args())
    assert "network_export_manifest_stale" in json.dumps(caught.value.to_payload())


@pytest.mark.parametrize(
    "arguments",
    [
        {k: v for k, v in _args().items() if k != "network_ref"},
        _args(network_ref=12),
        _args(network_ref="12"),
        _args(network_ref="nsolve_abc"),
        _args(network_ref="net_a/b"),
        {k: v for k, v in _args().items() if k != "manifest"},
        _args(manifest={}),
        _args(manifest="not an object"),
        {k: v for k, v in _args().items() if k != "node_ref"},
        _args(node_ref=""),
        {k: v for k, v in _args().items() if k != "representation_refs"},
        _args(representation_refs=[]),
        _args(representation_refs="nkin_a"),
        _args(representation_refs=["nkin_a", 3]),
        _args(representation_refs=["12"]),
        _args(format="zip"),
        _args(allow_administrative_choice="yes"),
        _args(naming_policy="smiles"),
        _args(energy_units=5),
        _args(profile="official"),
        _args(network_id=3),
        _args(solve_id=3),
        _args(kinetics_id=3),
        _args(rules=[{"rule_id": "mine"}]),
        _args(assessments={}),
        _args(eligible=True),
        _args(surprise=1),
    ],
)
def test_invalid_input_is_refused_before_any_request(arguments):
    seen, handler = _recording()
    with pytest.raises(MCPToolError) as caught:
        _call(_client(handler), arguments)
    assert caught.value.to_payload()["code"] == "invalid_input"
    assert seen == []


def test_arguments_sent_as_null_are_omitted_not_forwarded():
    seen, handler = _recording()
    _call(_client(handler), _args(format=None, allow_administrative_choice=None, energy_units=None, profile=None))
    assert json.loads(seen[0].content) == CHOICE


def test_a_null_required_argument_is_still_a_missing_one():
    seen, handler = _recording()
    with pytest.raises(MCPToolError):
        _call(_client(handler), _args(node_ref=None))
    assert seen == []
