"""``select_reaction_kinetics`` and its manifest sibling: the client's half of the contract.

Every test drives ``httpx.MockTransport``. What is asserted is the path, the verb, that the body carries only what
the caller supplied (the client invents no default of the question), that integer ids are refused before any request
is made, and that the server's answer comes back untouched. Behaviour parity with the live server is covered by
``backend/tests/api/scientific/test_kinetics_select_parity.py``.
"""

from __future__ import annotations

import json
from typing import get_args, get_type_hints
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client import KineticsSelectionResponse, TCKDBClient
from tckdb_client.errors import TCKDBHTTPError

ENTRY = "rxe_abc123def456"
QUESTION = {
    "direction": "forward",
    "target": {"kind": "whole_reaction"},
    "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure": {"kind": "independent"},
}


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
        client.select_reaction_kinetics(ENTRY, **QUESTION)
    (request,) = seen
    assert request.method == "POST"
    assert urlsplit(str(request.url)).path.endswith(f"/scientific/reaction-entries/{ENTRY}/kinetics/select")
    assert json.loads(request.content) == QUESTION
    assert "profile" not in parse_qs(urlsplit(str(request.url)).query)


def test_forwards_every_supplied_field_and_the_profile():
    seen, handler = _recorder()
    finite = {"kind": "finite", "min_bar": 1.0, "max_bar": 10.0}
    collider = {"components": [{"species_ref": "spc_abc", "mole_fraction": 0.79}, {"species_ref": "spc_def", "mole_fraction": 0.21}]}
    with _client(handler) as client:
        client.select_reaction_kinetics(
            ENTRY, **{**QUESTION, "pressure": finite}, collider=collider, policy="latest", mode="first",
            min_review_status="approved", phase="gas", profile="curated",
        )
    assert json.loads(seen[0].content) == {
        **QUESTION, "pressure": finite, "collider": collider, "policy": "latest", "mode": "first",
        "min_review_status": "approved", "phase": "gas",
    }
    assert parse_qs(urlsplit(str(seen[0].url)).query) == {"profile": ["curated"]}


def test_returns_the_servers_answer_untouched():
    answer = {"outcome": "incomparable_alternatives", "basis": "b", "selection": None, "fronts": [["kdet_a", "kdet_b"]]}
    _, handler = _recorder(answer)
    with _client(handler) as client:
        assert client.select_reaction_kinetics(ENTRY, **QUESTION) == answer


@pytest.mark.parametrize("bad", [12, "12", "kin_abc", "spe_abc", "", None])
def test_an_integer_or_wrong_prefix_ref_is_refused_before_any_request(bad):
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError, match="rxe_"):
        client.select_reaction_kinetics(bad, **QUESTION)
    assert seen == []


def test_the_question_is_required():
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(TypeError):
        client.select_reaction_kinetics(ENTRY, direction="forward")  # type: ignore[call-arg]
    assert seen == []


def test_a_server_refusal_surfaces_as_a_typed_http_error_with_its_status():
    refusal = {"code": "kinetics_selection_population_too_large", "detail": "d", "context": {"limit": 500}}
    _, handler = _recorder(refusal, status=422)
    with _client(handler) as client, pytest.raises(TCKDBHTTPError) as caught:
        client.select_reaction_kinetics(ENTRY, **QUESTION)
    assert caught.value.status_code == 422


def test_the_manifest_method_posts_to_the_manifest_path_and_forwards_the_profile():
    seen, handler = _recorder({"manifest_format_version": 1})
    with _client(handler) as client:
        out = client.get_reaction_kinetics_selection_manifest(ENTRY, **QUESTION, policy="default", profile="curated")
    assert out == {"manifest_format_version": 1}
    assert urlsplit(str(seen[0].url)).path.endswith(f"/reaction-entries/{ENTRY}/kinetics/select/manifest")
    assert json.loads(seen[0].content) == {**QUESTION, "policy": "default"}
    assert parse_qs(urlsplit(str(seen[0].url)).query) == {"profile": ["curated"]}


def test_the_manifest_method_refuses_an_integer_id_too():
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError, match="rxe_"):
        client.get_reaction_kinetics_selection_manifest(7, **QUESTION)
    assert seen == []


def test_the_response_type_names_the_outcome_vocabulary():
    outcomes = set(get_args(get_type_hints(KineticsSelectionResponse)["outcome"]))
    assert outcomes == {
        "policy_preferred", "incomparable_alternatives", "sole_eligible_candidate",
        "no_applicable_candidate", "policy_conflict",
    }


def test_the_manifest_method_forwards_phase_so_a_conflicting_one_reaches_the_server():
    seen, handler = _recorder({"manifest_format_version": 1})
    with _client(handler) as client:
        client.get_reaction_kinetics_selection_manifest(ENTRY, **QUESTION, phase="liquid")
    assert json.loads(seen[0].content) == {**QUESTION, "phase": "liquid"}


def test_both_methods_take_the_same_question_parameters():
    import inspect

    select = set(inspect.signature(TCKDBClient.select_reaction_kinetics).parameters)
    manifest = set(inspect.signature(TCKDBClient.get_reaction_kinetics_selection_manifest).parameters)
    assert select == manifest


def test_the_path_ref_is_url_quoted_like_the_mcp_does_it():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.select_reaction_kinetics("rxe_a/b?c", **QUESTION)
        client.get_reaction_kinetics_selection_manifest("rxe_a/b?c", **QUESTION)
    paths = [str(r.url).split("?")[0] for r in seen]
    assert all("rxe_a%2Fb%3Fc" in p for p in paths), paths
    assert paths[1].endswith("/kinetics/select/manifest")
