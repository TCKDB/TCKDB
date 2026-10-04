"""``select_network_kinetics`` and its manifest sibling: the client's half of the contract.

Every test drives ``httpx.MockTransport``. What is asserted is the path, the verb, that the body carries only what
the caller supplied (the client invents no default of the question and never sends a null), that integer ids are
refused before any request is made, that the path ref is URL-quoted, and that the server's answer comes back
untouched.
"""

from __future__ import annotations

import inspect
import json
from typing import get_args, get_type_hints
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client import NetworkSelectionResponse, TCKDBClient
from tckdb_client.errors import TCKDBHTTPError

NETWORK = "net_abc123def456"
QUESTION = {
    "coefficient_basis": "kernel",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure_min_bar": 0.5,
    "pressure_max_bar": 5.0,
    "bath": {"components": [{"species_ref": "spe_abc"}]},
    "partition": {"retained": ["a" * 64]},
}
CHANNEL = {"channel_key": "assoc", "observable": "product_resolved_coefficient"}


def _client(handler) -> TCKDBClient:
    client, _ = make_client(handler)
    return client


def _recorder(answer: dict | None = None, status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer if answer is not None else {"outcome": "policy_preferred"})

    return seen, handler


def test_posts_the_question_to_the_select_path_without_inventing_defaults():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.select_network_kinetics(NETWORK, **QUESTION, **CHANNEL)
    (request,) = seen
    assert request.method == "POST"
    assert urlsplit(str(request.url)).path.endswith(f"/scientific/networks/{NETWORK}/kinetics/select")
    assert json.loads(request.content) == {**QUESTION, **CHANNEL}  # no scope, objective, policy or mode of ours
    assert "profile" not in parse_qs(urlsplit(str(request.url)).query)


def test_channel_key_is_a_body_field_and_never_part_of_the_path():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.select_network_kinetics(NETWORK, **QUESTION, **CHANNEL)
    assert "assoc" not in urlsplit(str(seen[0].url)).path
    assert json.loads(seen[0].content)["channel_key"] == "assoc"


def test_none_valued_optionals_are_dropped_not_sent_as_null():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.select_network_kinetics(
            NETWORK, **QUESTION, **CHANNEL, scope=None, outputs=None, boundaries=None, regime=None, policy=None,
            mode=None, objective=None, reference_model_ref=None, min_review_status=None, phase=None,
        )
    body = json.loads(seen[0].content)
    assert body == {**QUESTION, **CHANNEL}
    assert None not in body.values()


def test_forwards_every_supplied_field_and_the_profile():
    seen, handler = _recorder()
    outputs = [
        {"channel_key": "elim", "observable": "product_resolved_coefficient"},
        {"channel_key": "assoc", "observable": "product_resolved_coefficient"},
    ]
    boundaries = [{"state": "c" * 64, "kind": "absorbing"}]
    regime = {"kind": "time_independent"}
    with _client(handler) as client:
        client.select_network_kinetics(
            NETWORK, **QUESTION, scope="projected_bundle", outputs=outputs, degeneracy_applied=True, boundaries=boundaries,
            regime=regime, source_composition_hash="a" * 64, sink_composition_hash="b" * 64,
            objective="model_fidelity", reference_model_ref="nsolve_xyz", policy="latest", mode="first",
            min_review_status="approved", phase="gas", profile="curated",
        )
    assert json.loads(seen[0].content) == {
        **QUESTION, "scope": "projected_bundle", "outputs": outputs, "degeneracy_applied": True,
        "boundaries": boundaries, "regime": regime, "source_composition_hash": "a" * 64,
        "sink_composition_hash": "b" * 64, "objective": "model_fidelity", "reference_model_ref": "nsolve_xyz",
        "policy": "latest", "mode": "first", "min_review_status": "approved", "phase": "gas",
    }
    assert parse_qs(urlsplit(str(seen[0].url)).query) == {"profile": ["curated"]}


def test_the_callers_mappings_are_copied_not_shared():
    seen, handler = _recorder()
    bath = {"components": [{"species_ref": "spe_abc"}]}
    with _client(handler) as client:
        client.select_network_kinetics(NETWORK, **{**QUESTION, "bath": bath}, **CHANNEL)
    bath["components"].append({"species_ref": "spe_later"})  # type: ignore[arg-type]
    assert json.loads(seen[0].content)["bath"] == {"components": [{"species_ref": "spe_abc"}]}


def test_returns_the_servers_answer_untouched():
    answer = {"outcome": "incomparable_alternatives", "basis": "b", "selection": None, "fronts": [["kdet_a", "kdet_b"]]}
    _, handler = _recorder(answer)
    with _client(handler) as client:
        assert client.select_network_kinetics(NETWORK, **QUESTION, **CHANNEL) == answer


@pytest.mark.parametrize("bad", [12, "12", "nsolve_abc", "spe_abc", "", None])
def test_an_integer_or_wrong_prefix_ref_is_refused_before_any_request(bad):
    seen, handler = _recorder()
    with _client(handler) as client:
        with pytest.raises(ValueError, match="net_"):
            client.select_network_kinetics(bad, **QUESTION, **CHANNEL)
        with pytest.raises(ValueError, match="net_"):
            client.get_network_kinetics_selection_manifest(bad, **QUESTION, **CHANNEL)
    assert seen == []


def test_the_question_is_required():
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(TypeError):
        client.select_network_kinetics(NETWORK, coefficient_basis="kernel")  # type: ignore[call-arg]
    assert seen == []


def test_a_server_refusal_surfaces_as_a_typed_http_error_with_its_status():
    refusal = {"code": "network_selection_population_too_large", "detail": "d", "context": {"limit": 200}}
    _, handler = _recorder(refusal, status=422)
    with _client(handler) as client, pytest.raises(TCKDBHTTPError) as caught:
        client.select_network_kinetics(NETWORK, **QUESTION, **CHANNEL)
    assert caught.value.status_code == 422


def test_the_manifest_method_posts_to_the_manifest_path_and_forwards_the_profile():
    seen, handler = _recorder({"manifest_format_version": 1})
    with _client(handler) as client:
        out = client.get_network_kinetics_selection_manifest(NETWORK, **QUESTION, **CHANNEL, policy="default", profile="curated")
    assert out == {"manifest_format_version": 1}
    assert urlsplit(str(seen[0].url)).path.endswith(f"/networks/{NETWORK}/kinetics/select/manifest")
    assert json.loads(seen[0].content) == {**QUESTION, **CHANNEL, "policy": "default"}
    assert parse_qs(urlsplit(str(seen[0].url)).query) == {"profile": ["curated"]}


def test_the_response_type_names_the_outcome_vocabulary():
    outcomes = set(get_args(get_type_hints(NetworkSelectionResponse)["outcome"]))
    assert outcomes == {
        "policy_preferred", "incomparable_alternatives", "sole_eligible_candidate",
        "no_applicable_candidate", "policy_conflict",
    }


def test_both_methods_take_the_same_question_parameters():
    select = set(inspect.signature(TCKDBClient.select_network_kinetics).parameters)
    manifest = set(inspect.signature(TCKDBClient.get_network_kinetics_selection_manifest).parameters)
    assert select == manifest and {"network_ref", "channel_key", "outputs", "bath", "partition"} <= select


def test_the_path_ref_is_url_quoted():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.select_network_kinetics("net_a/b?c", **QUESTION, **CHANNEL)
        client.get_network_kinetics_selection_manifest("net_a/b?c", **QUESTION, **CHANNEL)
    paths = [str(r.url).split("?")[0] for r in seen]
    assert all("net_a%2Fb%3Fc" in p for p in paths), paths
    assert paths[1].endswith("/kinetics/select/manifest")
