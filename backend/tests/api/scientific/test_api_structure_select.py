"""The structure selection routes: the HTTP contract.

The assessor, decision, rules and manifest are tested under ``tests/services/structure_selection``. These tests hold
what only the routes can break: a strict request (nothing a caller can set that is not theirs to set), public refs
only, an outcome for every question the route is for, visibility under the read profile, a manifest that downloads and
replays, and no database id anywhere in either document.
"""

from __future__ import annotations

import json

import pytest

from app.db.models.common import StructureDeterminationTargetKind
from app.services.structure_selection import replay_matches
from tests.services.scientific_read._factories import make_geometry
from tests.services.structure_selection._support import declaration, make_ts_world, make_world
from tests.services.structure_selection.test_service import build_basin

API = "/api/v1/scientific"


def calc_url(entry, *, manifest: bool = False) -> str:
    return f"{API}/species-entries/{entry.public_ref}/calculations/select" + ("/manifest" if manifest else "")


def conformer_url(entry, *, manifest: bool = False) -> str:
    return f"{API}/species-entries/{entry.public_ref}/conformers/select" + ("/manifest" if manifest else "")


def evidence_url(entry, *, manifest: bool = False) -> str:
    return f"{API}/transition-state-entries/{entry.public_ref}/evidence/select" + ("/manifest" if manifest else "")


@pytest.fixture
def calc_world(db_session):
    world = make_world(db_session)
    world.sp("a", -76.40, declared=declaration())
    world.sp("b", -76.45, declared=declaration())
    world.sp("c", None, declared=declaration())
    world.settle()
    return world


@pytest.fixture
def basin_world(db_session):
    world = make_world(db_session)
    build_basin(world, energy=-76.40, geometry=make_geometry(db_session))
    build_basin(world, energy=-76.45, geometry=make_geometry(db_session))
    world.settle()
    return world


def code(response) -> str | None:
    body = response.json()
    return body.get("code") if isinstance(body, dict) else None


def keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from keys(v)


# ---------------------------------------------------------------------------
# What each route answers
# ---------------------------------------------------------------------------


def test_the_calculation_route_returns_the_lowest_comparable_recorded_value_and_says_what_it_is_conditional_on(
    client, calc_world
):
    response = client.post(calc_url(calc_world.entry), json={})
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["outcome"] == "recorded_minimum"
    assert out["selected_refs"] == [calc_world.calcs["b"].public_ref]
    assert out["request"]["entry_ref"] == calc_world.entry.public_ref and out["request"]["intent"] == "recorded_minimum"
    assert out["request"]["profile"] == "exploratory" and out["request"]["bounds"]["candidates"] == 500
    assert out["coverage"]["authorized_population_only"] is True and "exhaustive" in out["search_completeness"]
    by_ref = {u["unit_ref"]: u for u in out["units"]}
    assert by_ref[calc_world.calcs["b"].public_ref]["eligible"] is True
    assert by_ref[calc_world.calcs["c"].public_ref]["applicability"] == "unresolved"  # a null energy is not zero
    assert out["integrity"]["algorithm"] == "sha256" and "not signatures" in out["integrity"]["note"]
    assert out["disclosures"]["unresolved_refs"] == [calc_world.calcs["c"].public_ref]


def test_the_conformer_route_orders_validated_basins_and_labels_the_outcome(client, basin_world):
    response = client.post(conformer_url(basin_world.entry), json={})
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["outcome"] == "validated_corpus_minimum" and len(out["selected_refs"]) == 1
    assert out["request"]["validation_claim"] == "local_minimum"  # the grain's conventional characterization
    assert "conformational-search" in out["basis"]
    assert out["cohorts"][0]["minimum_hartree"] == -76.45


def test_the_transition_state_route_takes_an_entry_and_qualifies_a_saddle_without_a_number(client, db_session):
    world = make_ts_world(db_session)
    saddle = StructureDeterminationTargetKind.saddle_point
    good, *_ = build_basin(world, energy=-40.2, target=saddle, geometry=make_geometry(db_session), n_freq=(-1500.0, 100.0, 200.0))
    flat, *_ = build_basin(world, energy=-40.3, target=saddle, geometry=make_geometry(db_session), n_freq=(100.0, 200.0))
    world.settle()
    body = {"intent": "qualify_evidence", "quantity": None, "validation_claim": "first_order_saddle"}
    response = client.post(evidence_url(world.entry), json=body)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["request"]["entry_ref"] == world.entry.public_ref and out["request"]["quantity"] is None and out["cohorts"] == []
    assert out["outcome"] == "qualified_evidence" and out["selected_refs"] == [good.public_ref]  # a flat geometry is not a saddle
    refuted = {u["unit_ref"]: u for u in out["units"]}[flat.public_ref]
    assert refuted["eligible"] is False and "curvature_contradicts_claim" in refuted["blocking"]
    lowest = client.post(evidence_url(world.entry), json={}).json()  # default: validated_saddle, electronic energy
    assert lowest["outcome"] == "validated_corpus_minimum" and lowest["selected_refs"] == [good.public_ref]
    assert lowest["request"]["validation_claim"] == "first_order_saddle"


def test_a_protocol_request_with_the_shipped_registry_ranks_nothing_and_lists_the_inactive_rules(client, db_session):
    world = make_world(db_session)
    from tests.services.scientific_read._factories import make_lot
    from tests.services.structure_selection.test_manifest import DECL_B

    other = make_lot(db_session)
    first, second = make_geometry(db_session), make_geometry(db_session)
    build_basin(world, energy=-76.40, geometry=first)
    build_basin(world, energy=-76.41, geometry=second)
    build_basin(world, energy=-76.50, geometry=first, lot=other, declared=DECL_B)
    build_basin(world, energy=-76.51, geometry=second, lot=other, declared=DECL_B)
    world.settle()
    response = client.post(conformer_url(world.entry), json={"intent": "protocol_preferred", "objective": "expected_accuracy"})
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["outcome"] == "incomparable_alternatives" and out["selected_refs"] == []
    matches = out["protocol"]["rule_matches"]
    assert {m["why"] for m in matches} == {"rule_status_inactive"} and len(matches) == 3
    assert response.json()["protocol"]["edges"] == []


# ---------------------------------------------------------------------------
# The manifest download
# ---------------------------------------------------------------------------


def test_the_manifest_downloads_replays_and_matches_the_response(client, calc_world):
    out = client.post(calc_url(calc_world.entry), json={}).json()
    response = client.post(calc_url(calc_world.entry, manifest=True), json={})
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"] and calc_world.entry.public_ref in response.headers["content-disposition"]
    manifest = response.json()
    assert replay_matches(manifest)
    assert manifest["outcome"] == out["outcome"] and manifest["integrity"]["manifest_sha256"] == out["integrity"]["manifest_sha256"]
    assert manifest["decision"]["selected_refs"] == out["selected_refs"]


def test_every_manifest_route_replays(client, calc_world, basin_world, db_session):
    assert replay_matches(client.post(conformer_url(basin_world.entry, manifest=True), json={}).json())
    ts = make_ts_world(db_session)
    build_basin(ts, energy=-40.2, target=StructureDeterminationTargetKind.saddle_point, geometry=make_geometry(db_session))
    ts.settle()
    assert replay_matches(client.post(evidence_url(ts.entry, manifest=True), json={}).json())


# ---------------------------------------------------------------------------
# A strict request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"bounds": {"candidates": 1_000_000}},
        {"manifest_bytes": 10**9},
        {"candidates": 10},
        {"limit": 5, "offset": 0},
        {"sort": "energy"},
        {"rules": [{"rule_id": "mine"}]},
        {"calculation_id": 1},
        {"species_entry_id": 1},
    ],
)
def test_nothing_a_caller_must_not_set_is_accepted(client, calc_world, extra):
    response = client.post(calc_url(calc_world.entry), json=extra)
    assert response.status_code == 422, response.text  # unknown fields are refused, never ignored


def test_an_intent_the_route_does_not_serve_is_refused(client, calc_world, basin_world):
    assert client.post(calc_url(calc_world.entry), json={"intent": "validated_minimum"}).status_code == 422
    assert client.post(conformer_url(basin_world.entry), json={"intent": "recorded_minimum"}).status_code == 422
    assert client.post(calc_url(calc_world.entry), json={"intent": "no_such_intent"}).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"intent": "protocol_preferred"},  # no objective
        {"objective": "expected_accuracy"},  # an objective with no protocol preference
        {"intent": "protocol_preferred", "objective": "model_fidelity"},  # no reference model
        {"member_refs": []},
        {"member_refs": ["calc_a", "calc_a"]},
        {"quantity": "kinetic_energy"},
        {"permitted_quality": ["unheard_of"]},
    ],
)
def test_a_request_that_contradicts_its_own_rules_is_a_422_naming_the_problem(client, calc_world, body):
    response = client.post(calc_url(calc_world.entry), json=body)
    assert response.status_code == 422, response.text


def test_evidence_qualification_asks_for_no_energy_and_a_compared_energy_must_be_stated(client, basin_world):
    refused = client.post(conformer_url(basin_world.entry), json={"intent": "qualify_evidence", "validation_claim": "local_minimum"})
    assert refused.status_code == 422  # the default quantity is an energy; qualification takes none
    ok = client.post(conformer_url(basin_world.entry), json={"intent": "qualify_evidence", "quantity": None, "validation_claim": "local_minimum"})
    assert ok.status_code == 200, ok.text
    assert client.post(conformer_url(basin_world.entry), json={"quantity": None}).status_code == 422


@pytest.mark.parametrize("name", ["enthalpy", "enthalpy_298", "gibbs_energy", "entropy", "heat_capacity", "barrier", "rate"])
def test_a_deferred_quantity_is_a_structured_refusal_not_an_energy_that_was_not_asked_for(client, calc_world, name):
    response = client.post(calc_url(calc_world.entry), json={"quantity": name})
    assert response.status_code == 422 and code(response) == "structure_selection_unsupported", response.text
    assert response.json()["context"] == {"reason": "deferred_quantity", "quantity": name}


def test_query_string_keys_other_than_the_profile_are_refused(client, calc_world):
    response = client.post(calc_url(calc_world.entry) + "?limit=5", json={})
    assert response.status_code == 422 and "post_search_fields_must_be_in_body" in response.text


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_only_public_refs_of_the_right_kind_resolve(client, calc_world, db_session):
    entry = calc_world.entry
    assert client.post(f"{API}/species-entries/{entry.id}/calculations/select", json={}).status_code == 422  # an integer id
    unknown = client.post(f"{API}/species-entries/spe_{'0' * 26}/calculations/select", json={})
    assert unknown.status_code == 404
    ts = make_ts_world(db_session)
    wrong_kind = client.post(f"{API}/species-entries/{ts.entry.public_ref}/calculations/select", json={})
    assert wrong_kind.status_code == 422 and code(wrong_kind) == "handle_type_mismatch"
    wrong_route = client.post(f"{API}/transition-state-entries/{entry.public_ref}/evidence/select", json={})
    assert wrong_route.status_code == 422 and code(wrong_route) == "handle_type_mismatch"


def test_a_fixed_geometry_or_member_that_names_nothing_is_a_404_not_an_empty_answer(client, calc_world):
    missing_geometry = client.post(calc_url(calc_world.entry), json={"geometry_ref": f"geom_{'0' * 26}"})
    assert missing_geometry.status_code == 404
    missing_member = client.post(calc_url(calc_world.entry), json={"member_refs": [f"calc_{'0' * 26}"]})
    assert missing_member.status_code == 404 and code(missing_member) == "unknown_structure_member_ref"


# ---------------------------------------------------------------------------
# Visibility and the profile
# ---------------------------------------------------------------------------


def test_under_the_curated_profile_nothing_below_the_floor_is_listed_or_counted_and_the_manifest_still_replays(
    client, db_session
):
    world = make_world(db_session)
    world.sp("approved", -76.40, declared=declaration())
    world.sp("pending", -76.99, declared=declaration(), status=None)
    world.settle()
    exploratory = client.post(calc_url(world.entry), json={"min_review_status": "approved"}).json()
    assert exploratory["disclosures"]["excluded_by_review_withheld"] is False
    assert exploratory["disclosures"]["excluded_count"] == 1
    assert world.calcs["pending"].public_ref in json.dumps(exploratory["disclosures"]["excluded_by_review"])
    curated = client.post(calc_url(world.entry), json={}, params={"profile": "curated"})
    assert curated.status_code == 200, curated.text
    out = curated.json()
    d = out["disclosures"]
    assert d["excluded_by_review_withheld"] is True and d["excluded_count"] == 0 and d["excluded_by_review"] == []
    assert out["request"]["profile"] == "curated" and out["review"]["effective_statuses"] == ["approved"]
    assert world.calcs["pending"].public_ref not in json.dumps(out)
    manifest = client.post(calc_url(world.entry, manifest=True), json={}, params={"profile": "curated"}).json()
    assert manifest["population"]["excluded_by_review_withheld"] is True
    assert manifest["population"]["total_units"] == manifest["population"]["visible_units"]
    assert world.calcs["pending"].public_ref not in json.dumps(manifest)
    assert replay_matches(manifest)  # the redaction was made before the checksum, so the download still verifies


def test_an_entry_the_profile_hides_is_a_404_exactly_like_one_that_does_not_exist(client, db_session):
    world = make_world(db_session, entry_status=None)
    world.sp("a", -76.4, declared=declaration())
    world.settle()
    assert client.post(calc_url(world.entry), json={}).status_code == 200
    hidden = client.post(calc_url(world.entry), json={}, params={"profile": "curated"})
    missing = client.post(f"{API}/species-entries/spe_{'0' * 26}/calculations/select", json={}, params={"profile": "curated"})
    assert hidden.status_code == missing.status_code == 404


# ---------------------------------------------------------------------------
# No database id leaves the building
# ---------------------------------------------------------------------------


def test_no_database_id_appears_in_either_document(client, basin_world):
    out = client.post(conformer_url(basin_world.entry), json={}).json()
    manifest = client.post(conformer_url(basin_world.entry, manifest=True), json={}).json()
    for document in (out, manifest):
        found = {k for k in keys(document) if k == "id" or (k.endswith("_id") and k not in {"cohort_id", "rule_id"})}
        assert found == set(), found
        text = json.dumps(document)
        assert "calculation_id" not in text and "species_entry_id" not in text
    assert out["request"]["entry_ref"].startswith("spe_")


def test_the_service_call_insists_on_a_snapshot_unless_the_test_harness_opted_out(db_session, calc_world):
    """Only the harness may read without a snapshot; a session that is not one is refused, never quietly answered."""
    from app.schemas.reads.scientific_structure_selection import CalculationSelectionRequest
    from app.services.read_snapshot import SnapshotNotConsistentError
    from app.services.structure_selection.public import SNAPSHOT_OPT_OUT, run_selection

    body = CalculationSelectionRequest()
    db_session.info.pop(SNAPSHOT_OPT_OUT, None)
    with pytest.raises(SnapshotNotConsistentError):
        run_selection(db_session, entry_id=calc_world.entry.id, body=body)
    db_session.info[SNAPSHOT_OPT_OUT] = True
    assert run_selection(db_session, entry_id=calc_world.entry.id, body=body).decision is not None
