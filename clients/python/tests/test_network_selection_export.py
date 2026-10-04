"""``export_selected_network_kinetics``: the client's half of the contract.

Every test drives ``httpx.MockTransport``. What is asserted is the path and verb, that the body carries only what the
caller supplied (no default of ours: in particular ``allow_administrative_choice`` is never sent unless given), that
integer ids and non-``net_`` refs are refused before any request, that ``None`` options are dropped, and that the
server's answer, or its structured refusal, comes back untouched.
"""

from __future__ import annotations

import inspect
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client import NetworkSelectedKineticsExport, TCKDBClient
from tckdb_client.errors import TCKDBHTTPError

NETWORK = "net_abc123def456"
MANIFEST = {"manifest_format_version": 1, "digest": {"algorithm": "sha256", "value": "0" * 64}}
CHOICE = {"manifest": MANIFEST, "node_ref": "nkdet_x", "representation_refs": ["nkin_a", "nkin_b"]}


def _client(handler) -> TCKDBClient:
    client, _ = make_client(handler)
    return client


def _recorder(answer: dict | None = None, status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer if answer is not None else {"format": "native"})

    return seen, handler


def test_posts_the_choice_to_the_export_path_with_no_defaults_of_ours():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.export_selected_network_kinetics(NETWORK, **CHOICE)
    (request,) = seen
    assert request.method == "POST"
    assert urlsplit(str(request.url)).path.endswith(f"/scientific/networks/{NETWORK}/kinetics/export-selected")
    body = json.loads(request.content)
    assert body == CHOICE
    assert "allow_administrative_choice" not in body and "format" not in body  # the server's defaults stand
    assert "profile" not in parse_qs(urlsplit(str(request.url)).query)


def test_forwards_every_supplied_option_and_the_profile_and_drops_none():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.export_selected_network_kinetics(
            NETWORK, **CHOICE, format="chemkin", allow_administrative_choice=True, energy_units="kj/mol",
            naming_policy="public_ref", profile="curated",
        )
        client.export_selected_network_kinetics(
            NETWORK, **CHOICE, format=None, allow_administrative_choice=None, energy_units=None, naming_policy=None
        )
    assert json.loads(seen[0].content) == {
        **CHOICE, "format": "chemkin", "allow_administrative_choice": True, "energy_units": "kj/mol",
        "naming_policy": "public_ref",
    }
    assert parse_qs(urlsplit(str(seen[0].url)).query) == {"profile": ["curated"]}
    assert json.loads(seen[1].content) == CHOICE


def test_explicit_false_is_sent_as_false_not_dropped():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.export_selected_network_kinetics(NETWORK, **CHOICE, allow_administrative_choice=False)
    assert json.loads(seen[0].content)["allow_administrative_choice"] is False


def test_the_callers_manifest_and_refs_are_copied_not_shared():
    seen, handler = _recorder()
    refs = ["nkin_a"]
    manifest = dict(MANIFEST)
    with _client(handler) as client:
        client.export_selected_network_kinetics(NETWORK, manifest=manifest, node_ref="n", representation_refs=refs)
    refs.append("nkin_later")
    manifest["extra"] = 1
    body = json.loads(seen[0].content)
    assert body["representation_refs"] == ["nkin_a"] and "extra" not in body["manifest"]


def test_returns_the_servers_answer_untouched():
    answer = {"format": "native", "members": [{"determination_ref": "nkdet_x"}], "files": None}
    _, handler = _recorder(answer)
    with _client(handler) as client:
        assert client.export_selected_network_kinetics(NETWORK, **CHOICE) == answer


@pytest.mark.parametrize("bad", [12, "12", "nsolve_abc", "rxe_abc", "", None])
def test_an_integer_or_wrong_prefix_ref_is_refused_before_any_request(bad):
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError, match="net_"):
        client.export_selected_network_kinetics(bad, **CHOICE)
    assert seen == []


def test_the_choice_is_required():
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(TypeError):
        client.export_selected_network_kinetics(NETWORK, manifest=MANIFEST)  # type: ignore[call-arg]
    assert seen == []


@pytest.mark.parametrize(
    "code", ["network_export_manifest_stale", "network_export_choice_not_allowed", "network_export_unsupported_form"]
)
def test_a_structured_refusal_surfaces_with_its_code_and_status(code):
    _, handler = _recorder({"code": code, "detail": "d", "context": {"reason": "r"}}, status=422)
    with _client(handler) as client, pytest.raises(TCKDBHTTPError) as caught:
        client.export_selected_network_kinetics(NETWORK, **CHOICE)
    assert caught.value.status_code == 422 and caught.value.code == code


def test_the_path_ref_is_url_quoted():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.export_selected_network_kinetics("net_a/b?c", **CHOICE)
    assert "net_a%2Fb%3Fc" in str(seen[0].url).split("?")[0]


def test_the_signature_names_the_choice_and_no_rule_or_id_parameter():
    params = set(inspect.signature(TCKDBClient.export_selected_network_kinetics).parameters)
    assert {"manifest", "node_ref", "representation_refs", "allow_administrative_choice"} <= params
    assert not [p for p in params if p.endswith("_id") or "rule" in p]
    assert "format" in NetworkSelectedKineticsExport.__annotations__


def test_include_reported_is_forwarded_only_when_given():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.export_selected_network_kinetics(NETWORK, **CHOICE, format="chemkin", include_reported=True)
        client.export_selected_network_kinetics(NETWORK, **CHOICE, include_reported=None)
    assert json.loads(seen[0].content)["include_reported"] is True
    assert "include_reported" not in json.loads(seen[1].content)
