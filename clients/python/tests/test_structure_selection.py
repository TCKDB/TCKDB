"""The structure selection methods and their manifest siblings: the client's half of the contract.

Every test drives ``httpx.MockTransport``. What is asserted is the path, the verb, that the body carries only what the
caller supplied (the client invents no default of the question), that ``quantity=None`` is the one value sent as JSON
null (evidence-only qualification), that integer ids and any bound, page, sort or rule are refused before a request is
made, and that the server's answer comes back untouched. Behaviour parity with the live server is covered by
``backend/tests/api/scientific/test_api_structure_select.py``.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client import StructureSelectionResponse, TCKDBClient

SPECIES = "spe_abc123def456"
TSE = "tse_abc123def456"


def _client(handler) -> TCKDBClient:
    client, _ = make_client(handler)
    return client


def _recorder(answer: dict | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer if answer is not None else {"outcome": "recorded_minimum"})

    return seen, handler


CALLS = [
    ("select_species_calculations", SPECIES, f"/scientific/species-entries/{SPECIES}/calculations/select"),
    ("select_conformer_basins", SPECIES, f"/scientific/species-entries/{SPECIES}/conformers/select"),
    ("select_transition_state_evidence", TSE, f"/scientific/transition-state-entries/{TSE}/evidence/select"),
]
MANIFESTS = [
    ("get_species_calculation_selection_manifest", SPECIES, f"/scientific/species-entries/{SPECIES}/calculations/select/manifest"),
    ("get_species_conformer_selection_manifest", SPECIES, f"/scientific/species-entries/{SPECIES}/conformers/select/manifest"),
    ("get_transition_state_evidence_selection_manifest", TSE, f"/scientific/transition-state-entries/{TSE}/evidence/select/manifest"),
]


@pytest.mark.parametrize(("method", "ref", "path"), CALLS)
def test_posts_to_the_select_path_with_an_empty_body_when_nothing_is_supplied(method, ref, path):
    seen, handler = _recorder()
    with _client(handler) as client:
        getattr(client, method)(ref)
    (request,) = seen
    assert request.method == "POST" and urlsplit(str(request.url)).path.endswith(path)
    assert json.loads(request.content) == {}  # no default of ours: the server's defaults apply
    assert "profile" not in parse_qs(urlsplit(str(request.url)).query)


@pytest.mark.parametrize(("method", "ref", "path"), MANIFESTS)
def test_the_manifest_methods_post_the_same_request_to_the_manifest_path(method, ref, path):
    seen, handler = _recorder({"manifest_format_version": 1})
    with _client(handler) as client:
        out = getattr(client, method)(ref, intent="qualify_evidence", quantity=None, validation_claim="local_minimum", profile="curated")
    (request,) = seen
    assert urlsplit(str(request.url)).path.endswith(path)
    assert json.loads(request.content) == {"intent": "qualify_evidence", "quantity": None, "validation_claim": "local_minimum"}
    assert parse_qs(urlsplit(str(request.url)).query) == {"profile": ["curated"]}
    assert out == {"manifest_format_version": 1}


def test_quantity_none_is_json_null_and_every_other_none_is_not_sent():
    seen, handler = _recorder()
    with _client(handler) as client:
        client.select_conformer_basins(SPECIES, intent="qualify_evidence", quantity=None, validation_claim="local_minimum",
                                         geometry_ref=None, member_refs=None, objective=None)
        client.select_conformer_basins(SPECIES, intent="validated_minimum")
    assert json.loads(seen[0].content) == {"intent": "qualify_evidence", "quantity": None, "validation_claim": "local_minimum"}
    assert json.loads(seen[1].content) == {"intent": "validated_minimum"}  # quantity omitted, not null


def test_forwards_every_supplied_field():
    seen, handler = _recorder()
    recipe = {"version": 1, "source": {"origin": "producer_declared"}, "spin_treatment": {"state": "known", "value": "restricted"}}
    with _client(handler) as client:
        client.select_transition_state_evidence(
            TSE, intent="protocol_preferred", quantity="zero_kelvin_energy", coverage_requirement="all_requested_members",
            validation_claim="first_order_saddle", min_review_status="approved", permitted_quality=("raw", "curated"),
            geometry_ref="geom_x", member_refs=("sdet_a", "sdet_b"), recipe=recipe, require_stable_reference=True,
            require_connectivity=True, administrative_policy="latest", result_mode="first", apply_rules=False,
            objective="model_fidelity", reference_model="ref@1", repeat_policy="administrative_representative",
            profile="curated",
        )
    assert json.loads(seen[0].content) == {
        "intent": "protocol_preferred", "quantity": "zero_kelvin_energy", "coverage_requirement": "all_requested_members",
        "validation_claim": "first_order_saddle", "min_review_status": "approved", "permitted_quality": ["raw", "curated"],
        "geometry_ref": "geom_x", "member_refs": ["sdet_a", "sdet_b"], "recipe": recipe, "require_stable_reference": True,
        "require_connectivity": True, "administrative_policy": "latest", "result_mode": "first", "apply_rules": False,
        "objective": "model_fidelity", "reference_model": "ref@1", "repeat_policy": "administrative_representative",
    }
    assert parse_qs(urlsplit(str(seen[0].url)).query) == {"profile": ["curated"]}


def test_returns_the_servers_answer_untouched():
    answer = {"outcome": "incomparable_alternatives", "basis": "b", "selected_refs": [], "cohorts": [{"cohort_id": "coh_x"}]}
    _, handler = _recorder(answer)
    with _client(handler) as client:
        assert client.select_species_calculations(SPECIES) == answer


@pytest.mark.parametrize(
    ("method", "bad"),
    [
        ("select_species_calculations", 123),
        ("select_species_calculations", "tse_abc"),  # a transition state entry ref on a species route
        ("select_conformer_basins", "rxe_abc"),
        ("select_conformer_basins", 5),
        ("select_transition_state_evidence", "spe_abc"),
        ("select_transition_state_evidence", 7),
    ],
)
def test_integer_ids_and_refs_of_the_wrong_kind_are_refused_before_any_request(method, bad):
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError, match="must be a public"):
        getattr(client, method)(bad)
    assert seen == []


@pytest.mark.parametrize("field", ["bounds", "limit", "offset", "page", "page_size", "cursor", "max_candidates", "manifest_bytes", "sort", "rules"])
def test_no_bound_page_sort_or_rule_can_be_carried(field):
    seen, handler = _recorder()
    with _client(handler) as client, pytest.raises(ValueError, match="not request fields"):
        client.get_species_calculation_selection_manifest(SPECIES, **{field: 1})
    assert seen == []


def test_the_response_type_names_every_outcome_the_server_can_return():
    from typing import get_args

    from tckdb_client import StructureSelectionOutcomeToken

    assert set(get_args(StructureSelectionOutcomeToken)) == {
        "no_candidates", "energy_unavailable", "unresolved_comparability", "no_applicable_candidate", "recorded_minimum",
        "representative_minimum", "qualified_evidence", "validated_corpus_minimum", "policy_preferred",
        "sole_eligible_candidate", "incomparable_alternatives", "policy_conflict", "evidence_conflict",
    }
    assert StructureSelectionResponse.__required_keys__ >= {"outcome", "basis", "selected_refs", "coverage", "integrity"}
