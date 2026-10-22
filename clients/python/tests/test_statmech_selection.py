"""The statmech selection methods and their manifest sibling: the client's half of the contract.

Every test drives ``httpx.MockTransport``. What is asserted is the path, the verb, that the body is exactly what the
caller supplied (the client invents no default of the question and chooses no candidate), that an integer id, a ref of
the wrong kind and any bound, page, sort or rule are refused before a request is made, and that the server's answer comes
back untouched. Behaviour parity with the live server is covered by
``backend/tests/api/scientific/test_api_statmech_select.py``.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client import StatmechSelectionResponse, TCKDBClient, TCKDBHTTPError

SPECIES = "spe_abc123def456"
TSE = "tse_abc123def456"
BASIN = "cg_abc123def456"

BODY = {
    "intent": "qualify",
    "task": "within_basin_thermal",
    "observable": "entropy",
    "target": {
        "kind": "species_entry",
        "species_entry_ref": SPECIES,
        "scope": {"kind": "basin", "conformer_group_ref": BASIN},
        "isotope_convention": "entry_documented_masses",
        "electronic_scope": "entry_state",
        "stereochemical_scope": "entry_identity",
    },
    "conditions": {"temperature_domain_k": {"min": 298.15, "max": 500.0}},
}


def _client(handler) -> TCKDBClient:
    client, _ = make_client(handler)
    return client


def _recorder(answer: dict | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer if answer is not None else {"outcome": "qualified_models"})

    return seen, handler


def test_posts_the_request_untouched_to_the_select_path():
    seen, handler = _recorder()
    with _client(handler) as client:
        out = client.select_statmech_models(BODY)
    (request,) = seen
    assert request.method == "POST" and urlsplit(str(request.url)).path.endswith("/scientific/statmech/select")
    assert json.loads(request.content) == BODY  # no default of ours: the server's defaults apply
    assert "profile" not in parse_qs(urlsplit(str(request.url)).query)
    assert out == {"outcome": "qualified_models"}


def test_the_manifest_method_posts_the_same_request_to_the_manifest_path_with_the_profile():
    seen, handler = _recorder({"format": {"name": "x_manifest", "version": 1}})
    with _client(handler) as client:
        out = client.get_statmech_selection_manifest(BODY, profile="curated")
    (request,) = seen
    assert urlsplit(str(request.url)).path.endswith("/scientific/statmech/select/manifest")
    assert json.loads(request.content) == BODY and parse_qs(urlsplit(str(request.url)).query)["profile"] == ["curated"]
    assert out == {"format": {"name": "x_manifest", "version": 1}}


def test_a_transition_state_target_and_explicit_candidates_are_accepted():
    ts = {**BODY, "target": {**BODY["target"], "kind": "transition_state_entry", "transition_state_entry_ref": TSE}}
    ts["target"].pop("species_entry_ref")
    ts["population"] = {"kind": "model_revisions", "model_refs": ["smd_" + "a" * 26, "sm_" + "b" * 26]}
    seen, handler = _recorder()
    with _client(handler) as client:
        client.select_statmech_models(ts)
    assert json.loads(seen[0].content)["population"]["model_refs"][0].startswith("smd_")


@pytest.mark.parametrize(
    "bad",
    [
        {"bounds": {"candidates": 10**6}},
        {"manifest_bytes": 10**9},
        {"limit": 5},
        {"offset": 0},
        {"page": 2},
        {"sort": "energy"},
        {"rules": [{"rule_id": "mine"}]},
    ],
)
def test_a_bound_page_sort_or_rule_is_refused_before_any_request(bad):
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError, match="not request fields"):
        client.select_statmech_models({**BODY, **bad})
    assert seen == []


@pytest.mark.parametrize(
    "target",
    [
        {**BODY["target"], "species_entry_ref": 7},
        {**BODY["target"], "species_entry_ref": TSE},
        {**BODY["target"], "species_entry_ref": None},
        {**BODY["target"], "kind": "reaction_entry"},
    ],
)
def test_an_integer_id_or_a_ref_of_the_wrong_kind_is_refused_before_any_request(target):
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError):
        client.select_statmech_models({**BODY, "target": target})
    assert seen == []


def test_a_target_is_required_and_candidate_refs_must_be_public_refs():
    seen, handler = _recorder()
    with _client(handler) as client:
        with pytest.raises(ValueError, match="names its target"):
            client.select_statmech_models({"intent": "qualify"})
        for refs in ([12], [BASIN], ["spe_abc"]):
            with pytest.raises(ValueError, match="sm_"):
                client.select_statmech_models({**BODY, "population": {"kind": "model_revisions", "model_refs": refs}})
    assert seen == []


def test_the_caller_s_request_is_not_mutated_and_the_answer_comes_back_untouched():
    request = json.loads(json.dumps(BODY))
    answer = {"outcome": "sole_eligible_candidate", "selection": {"sole_eligible_ref": "smd_x"}, "notices": ["n"], "extra": [1]}
    seen, handler = _recorder(answer)
    with _client(handler) as client:
        out: StatmechSelectionResponse = client.select_statmech_models(request)
    assert request == BODY and out == answer


def test_a_server_refusal_is_raised_as_the_server_states_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422, json={"code": "statmech_selection_bound_exceeded", "detail": "too many", "context": {"bound": "candidates"}}
        )

    with _client(handler) as client, pytest.raises(TCKDBHTTPError) as caught:
        client.select_statmech_models(BODY)
    assert caught.value.status_code == 422 and caught.value.code == "statmech_selection_bound_exceeded"
