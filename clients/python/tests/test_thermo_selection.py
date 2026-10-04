"""``select_species_thermo`` and its manifest sibling: the client's half of the contract.

Every test drives ``httpx.MockTransport``. What is asserted is the path, the verb, that the
body carries only what the caller supplied (the client invents no default), that integer ids
are refused before any request is made, and that the server's answer comes back untouched.
Behaviour parity with the live server is covered by
``backend/tests/api/scientific/test_thermo_select_parity.py``.
"""

from __future__ import annotations

import json
from typing import get_args, get_type_hints
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client import TCKDBClient, ThermoSelectionResponse
from tckdb_client.errors import TCKDBHTTPError

ENTRY = "spe_abc123def456"
EQUILIBRIUM = {"kind": "equilibrium_ensemble"}


def _client(handler) -> TCKDBClient:
    client, _ = make_client(handler)
    return client


def _recorder(answer: dict | None = None, status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer if answer is not None else {"outcome": "policy_preferred"})

    return seen, handler


def test_posts_the_target_to_the_select_path_without_inventing_defaults():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.select_species_thermo(ENTRY, target=EQUILIBRIUM)
    (request,) = seen
    assert request.method == "POST"
    assert urlsplit(str(request.url)).path.endswith(f"/scientific/species-entries/{ENTRY}/thermo/select")
    assert json.loads(request.content) == {"target": EQUILIBRIUM}
    assert "profile" not in parse_qs(urlsplit(str(request.url)).query)


def test_forwards_every_supplied_field_and_the_profile():
    seen, handler = _recorder()
    target = {"kind": "single_conformer", "conformer_group_ref": "cg_abc"}
    with _client(handler) as client:
        client.select_species_thermo(
            ENTRY, target=target, policy="latest", result_mode="first", min_review_status="approved",
            temperature_k=298.15, phase="gas", profile="curated",
        )
    body = json.loads(seen[0].content)
    assert body == {
        "target": target, "policy": "latest", "result_mode": "first", "min_review_status": "approved",
        "temperature_k": 298.15, "phase": "gas",
    }
    assert parse_qs(urlsplit(str(seen[0].url)).query) == {"profile": ["curated"]}


def test_returns_the_servers_answer_untouched():
    answer = {"outcome": "incomparable_alternatives", "basis": "b", "selection": None, "fronts": [["thm_a", "thm_b"]]}
    _, handler = _recorder(answer)
    with _client(handler) as client:
        assert client.select_species_thermo(ENTRY, target=EQUILIBRIUM) == answer


@pytest.mark.parametrize("bad", [12, "12", "thm_abc", "cg_abc", "", None])
def test_an_integer_or_wrong_prefix_ref_is_refused_before_any_request(bad):
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError, match="spe_"):
        client.select_species_thermo(bad, target=EQUILIBRIUM)
    assert seen == []


def test_a_server_refusal_surfaces_as_a_typed_http_error_with_its_code():
    refusal = {"code": "thermo_selection_condition_conflict", "detail": "d", "context": {"field": "phase"}}
    _, handler = _recorder(refusal, status=422)
    with _client(handler) as client, pytest.raises(TCKDBHTTPError) as caught:
        client.select_species_thermo(ENTRY, target=EQUILIBRIUM, phase="aqueous")
    assert caught.value.status_code == 422


def test_the_manifest_method_forwards_the_profile():
    seen, handler = _recorder({"manifest_format_version": 1})
    with _client(handler) as client:
        client.get_species_thermo_selection_manifest(ENTRY, target=EQUILIBRIUM, profile="curated")
    assert parse_qs(urlsplit(str(seen[0].url)).query) == {"profile": ["curated"]}


def test_the_manifest_method_posts_to_the_manifest_path():
    seen, handler = _recorder({"manifest_format_version": 1})
    with _client(handler) as client:
        out = client.get_species_thermo_selection_manifest(ENTRY, target=EQUILIBRIUM, policy="default")
    assert out == {"manifest_format_version": 1}
    assert urlsplit(str(seen[0].url)).path.endswith(f"/species-entries/{ENTRY}/thermo/select/manifest")
    assert "format" not in parse_qs(urlsplit(str(seen[0].url)).query)
    assert json.loads(seen[0].content) == {"target": EQUILIBRIUM, "policy": "default"}


def test_the_response_type_names_the_outcome_vocabulary():
    outcomes = set(get_args(get_type_hints(ThermoSelectionResponse)["outcome"]))
    assert outcomes == {
        "policy_preferred", "incomparable_alternatives", "sole_eligible_candidate",
        "no_applicable_candidate", "policy_conflict",
    }
