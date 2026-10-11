"""The statmech selection route: the HTTP contract.

The assessor, decision, rules and manifest are tested under ``tests/services/statmech_selection``. These tests hold
what only the route can break: a strict request (nothing a caller can set that is not theirs to set), public refs only,
a scientific outcome for every question the route is for (a valid negative answer is a 200), coded refusals, visibility
under the read profile, the advisories surfaced in the response, a manifest that downloads and replays, and no database
id anywhere in either document.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.schemas.reads.scientific_statmech_selection import StatmechSelectionRequest
from app.services.statmech_selection import manifest as manifest_module
from app.services.statmech_selection.models import SelectionBounds
from tests.services.statmech_selection.test_request import THERMAL, make
from tests.services.statmech_selection.test_service import _literature, build, build_ts_scene

URL = "/api/v1/scientific/statmech/select"
PREFERRED = {"intent": "protocol_preferred", "comparison_objective": "expected_accuracy"}


def body_for(scene, **changes) -> dict:
    """The scene's thermal request; ``changes`` (``a__b`` paths allowed) override the scene's own refs."""
    defaults = {
        "target__species_entry_ref": scene.world.entry.public_ref,
        "target__scope__conformer_group_ref": scene.group.public_ref,
    }
    return make(THERMAL, **{**defaults, **changes})


def keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from keys(v)


def code(response) -> str | None:
    body = response.json()
    return body.get("code") if isinstance(body, dict) else None


# --- what the route answers ---------------------------------------------------------------------------------------------------


def test_a_qualify_request_lists_every_candidate_and_selects_nothing(client, db_session):
    scene = build(db_session)
    response = client.post(URL, json=body_for(scene))
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["outcome"] == "qualified_models"
    assert out["selection"] == {"scientific_ref": None, "sole_eligible_ref": None, "administrative_ref": None, "basis": None, "limitation": None}
    (candidate,) = out["candidates"]
    assert candidate["candidate_ref"].startswith("smd_") and candidate["kind"] == "declared_model" and candidate["eligible"] is True
    assert candidate["owner_ref"] == scene.world.entry.public_ref and candidate["reasons"] == []
    assert out["request"]["profile"] == "exploratory" and out["request"]["bounds"]["candidates"] == 500
    assert out["coverage"]["authorized_population"] == "complete" and out["coverage"]["physical_search"] == "not_established"
    assert out["population"]["visible_candidates"] == 1 and out["versions"]["contract"] == "statmech-selection-v1"
    assert out["review"]["effective_floor"] == "not_reviewed" and "approved" in out["review"]["effective_statuses"]
    assert out["integrity"]["algorithm"] == "sha256" and "not signatures" in out["integrity"]["note"]
    assert out["edges"] == [] and out["fronts"] == [] and out["conflicts"] == {"opposing_pairs": [], "cycles": [], "overridden_edges": []}


def test_a_lone_eligible_model_is_sole_not_preferred_and_the_registry_ranks_nothing(client, db_session):
    scene = build(db_session)
    out = client.post(URL, json=body_for(scene, **PREFERRED)).json()
    assert out["outcome"] == "sole_eligible_candidate"
    assert out["selection"]["sole_eligible_ref"] == out["candidates"][0]["candidate_ref"] and out["selection"]["scientific_ref"] is None
    assert out["selection"]["limitation"] and out["protocol"]["rules"] == []  # this release ships no rule


def test_a_legacy_record_is_a_candidate_named_by_its_own_ref(client, db_session):
    scene = build(db_session, declared=False)
    out = client.post(URL, json=body_for(scene)).json()
    (candidate,) = out["candidates"]
    assert candidate["kind"] == "legacy_record" and candidate["candidate_ref"].startswith("sm_")


def test_a_negative_scientific_outcome_is_a_200(client, db_session):
    scene = build(db_session)
    out = client.post(URL, json=body_for(scene, task="canonical_model", observable="internal_partition")).json()
    assert out["outcome"] in {"missing_required_data", "unresolved_applicability", "no_applicable_candidate", "unsupported_model"}
    assert out["selection"]["scientific_ref"] is None
    assert any(not c["eligible"] and c["reasons"] for c in out["candidates"])
    deferred = client.post(URL, json=body_for(scene, task="numerical_evaluation", observable="entropy"))
    assert deferred.status_code == 200 and deferred.json()["outcome"] == "unsupported_task" and deferred.json()["candidates"] == []


def test_explicit_candidates_are_named_by_public_ref_and_a_stranger_is_one_404(client, db_session):
    scene = build(db_session)
    other = build(db_session)
    (own,) = (c["candidate_ref"] for c in client.post(URL, json=body_for(scene)).json()["candidates"])
    (foreign,) = (c["candidate_ref"] for c in client.post(URL, json=body_for(other)).json()["candidates"])
    ok = client.post(URL, json=body_for(scene, population={"kind": "model_revisions", "model_refs": [own]}))
    assert ok.status_code == 200 and [c["candidate_ref"] for c in ok.json()["candidates"]] == [own]
    for ref in (foreign, "smd_" + "a" * 26, "sm_" + "a" * 26):
        response = client.post(URL, json=body_for(scene, population={"kind": "model_revisions", "model_refs": [ref]}))
        assert response.status_code == 404 and code(response) == "unknown_statmech_candidate_ref"


def test_a_transition_state_entry_is_a_target_through_the_same_route(client, db_session):
    scene = build_ts_scene(db_session)
    out = client.post(URL, json=scene.body)
    assert out.status_code == 200, out.text
    answer = out.json()
    assert answer["outcome"] == "qualified_models" and answer["request"]["target"]["kind"] == "transition_state_entry"
    (candidate,) = answer["candidates"]
    assert candidate["owner_ref"] == scene.entry.public_ref and candidate["eligible"] is True
    assert answer["request"]["target"]["scope"]["determination_ref"] == scene.determination.public_ref
    assert manifest_module.replay_matches(client.post(URL + "/manifest", json=scene.body).json())
    # a species-entry ref on a transition-state target is refused by the schema, not resolved as the wrong kind
    wrong = make(scene.body, target__transition_state_entry_ref=scene.world.entry.public_ref.replace("tse_", "spe_"))
    assert client.post(URL, json=wrong).status_code == 422


# --- advisories and notices travel with the answer ----------------------------------------------------------------------------


def test_imported_parameter_advisories_and_notices_are_in_the_response(client, db_session):
    scene = build(db_session, imported=_literature(db_session, doi="10.1000/imported"))
    out = client.post(URL, json=body_for(scene)).json()
    (candidate,) = out["candidates"]
    assert {"imported_parameters_not_revalidated:geometry", "imported_parameters_not_revalidated:curvature"} <= set(candidate["advisory"])
    assert set(out["advisories"][candidate["candidate_ref"]]) >= set(candidate["advisory"])
    assert any("not re-judged" in n for n in out["notices"])


def test_a_title_only_citation_is_kept_but_meets_no_role_and_says_so(client, db_session):
    scene = build(db_session, imported=_literature(db_session))
    out = client.post(URL, json=body_for(scene)).json()
    (candidate,) = out["candidates"]
    assert candidate["eligible"] is False
    assert any(r["code"].startswith("required_source_unusable") for r in candidate["reasons"])
    assert any(a.startswith("source_literature_unidentified:") for a in candidate["advisory"])
    assert any("no DOI or ISBN" in n for n in out["notices"])


# --- the manifest ---------------------------------------------------------------------------------------------------------------


def test_the_manifest_downloads_replays_and_matches_the_response(client, db_session):
    scene = build(db_session)
    body = body_for(scene, **PREFERRED)
    out = client.post(URL, json=body).json()
    response = client.post(URL + "/manifest", json=body)
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"] and scene.world.entry.public_ref in response.headers["content-disposition"]
    manifest = response.json()
    assert manifest_module.replay_matches(manifest)
    assert manifest["outcome"] == out["outcome"] and manifest["integrity"]["manifest_sha256"] == out["integrity"]["manifest_sha256"]
    assert out["manifest"]["manifest_sha256"] == manifest["integrity"]["manifest_sha256"] and out["manifest"]["path"].endswith("/select/manifest")
    assert out["manifest"]["canonical_bytes"] == len(manifest_module.canonical(manifest).encode())
    assert manifest["decision"]["selection"] == out["selection"]


def test_no_database_id_appears_in_either_document(client, db_session):
    scene = build(db_session, imported=_literature(db_session, doi="10.1000/ids"))
    body = body_for(scene, **PREFERRED)
    for document in (client.post(URL, json=body).json(), client.post(URL + "/manifest", json=body).json()):
        assert not [k for k in keys(document) if k == "id" or k.endswith("_id")]


def test_under_the_curated_profile_what_the_floor_hides_is_neither_listed_nor_counted_nor_in_the_manifest(client, db_session):
    from app.db.models.common import RecordReviewStatus, ScientificOriginKind, SubmissionRecordType
    from app.db.models.statmech import Statmech
    from tests.services.statmech_selection.test_service import set_review

    scene = build(db_session)
    set_review(db_session, record_type=SubmissionRecordType.conformer_group, record_id=scene.group.id, status=RecordReviewStatus.approved)
    hidden = Statmech(species_entry_id=scene.world.entry.id, scientific_origin=ScientificOriginKind.computed)
    db_session.add(hidden)
    db_session.flush()
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=hidden.id, status=RecordReviewStatus.not_reviewed)
    body = body_for(scene)
    curated = client.post(URL + "?profile=curated", json=body)
    assert curated.status_code == 200, curated.text
    out = curated.json()
    assert out["request"]["profile"] == "curated" and out["review"]["effective_statuses"] == ["approved"]
    assert out["review"]["effective_floor"] == "approved"
    assert out["population"]["excluded_by_review"] == [] and out["population"]["excluded_count"] == 0
    assert out["population"]["excluded_by_review_withheld"] is True
    assert hidden.public_ref not in curated.text
    manifest = client.post(URL + "/manifest?profile=curated", json=body)
    assert hidden.public_ref not in manifest.text and manifest_module.replay_matches(manifest.json())
    exploratory = client.post(URL, json={**body, "min_review_status": "approved"}).json()
    assert exploratory["population"]["excluded_count"] == 1 and exploratory["population"]["excluded_by_review"][0]["candidate_ref"] == hidden.public_ref


# --- a strict request -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"bounds": {"candidates": 1_000_000}},
        {"manifest_bytes": 10**9},
        {"limit": 5, "offset": 0},
        {"sort": "energy"},
        {"rules": [{"rule_id": "mine"}]},
        {"statmech_id": 1},
        {"candidate_ids": [1]},
    ],
)
def test_nothing_a_caller_must_not_set_is_accepted(client, db_session, extra):
    scene = build(db_session)
    assert client.post(URL, json={**body_for(scene), **extra}).status_code == 422  # refused, never ignored


def test_a_ref_of_the_wrong_kind_or_an_integer_id_is_refused(client, db_session):
    scene = build(db_session)
    assert client.post(URL, json=body_for(scene, target__species_entry_ref=7)).status_code == 422
    assert client.post(URL, json=body_for(scene, target__species_entry_ref=scene.group.public_ref)).status_code == 422
    assert client.post(URL, json=body_for(scene, target__scope__conformer_group_ref=scene.world.entry.public_ref)).status_code == 422
    assert client.post(URL, json=body_for(scene, population={"kind": "model_revisions", "model_refs": [12]})).status_code == 422


def test_contradictory_or_misspelled_requests_are_422_not_a_quiet_default(client, db_session):
    scene = build(db_session)
    assert client.post(URL, json=body_for(scene, task="within_basin_thermel")).status_code == 422
    assert client.post(URL, json=body_for(scene, intent="qualify", comparison_objective="expected_accuracy")).status_code == 422
    assert client.post(URL, json=body_for(scene, intent="protocol_preferred")).status_code == 422
    assert client.post(URL, json=body_for(scene, observable="enthalpy")).status_code == 422
    assert client.post(URL, json=body_for(scene, conditions__temperature_domain_k={"min": 500, "max": 300})).status_code == 422


def test_query_string_search_fields_are_refused_on_a_post(client, db_session):
    scene = build(db_session)
    response = client.post(URL + "?limit=5", json=body_for(scene))
    assert response.status_code == 422 and "post_search_fields_must_be_in_body" in response.text
    assert client.post(URL + "?profile=exploratory", json=body_for(scene)).status_code == 200


# --- refusals and missing things -------------------------------------------------------------------------------------------------------


def test_an_unknown_target_basin_determination_or_declaration_is_a_404(client, db_session):
    scene = build(db_session)
    ghost = "a" * 26
    assert client.post(URL, json=body_for(scene, target__species_entry_ref="spe_" + ghost)).status_code == 404
    assert client.post(URL, json=body_for(scene, target__scope__conformer_group_ref="cg_" + ghost)).status_code == 404
    assert client.post(URL, json=body_for(scene, reference_model_ref="smd_" + ghost, **{"intent": "protocol_preferred", "comparison_objective": "model_fidelity"})).status_code == 404
    ts_body = make(
        THERMAL, task="conventional_ts_transverse_model", observable="transverse_partition",
        target={"kind": "transition_state_entry", "transition_state_entry_ref": "tse_" + ghost,
                "scope": {"kind": "ts_determination", "determination_ref": "sdet_" + ghost, "dividing_surface": "conventional_saddle_point", "reaction_coordinate_excluded": True},
                "isotope_convention": "entry_documented_masses", "electronic_scope": "entry_state", "stereochemical_scope": "entry_identity"},
    )
    assert client.post(URL, json=ts_body).status_code == 404


def test_an_unknown_determination_or_ensemble_alone_is_a_404_not_a_quiet_empty_answer(client, db_session):
    """Each ref is checked on its own: the target is real here, so only the named scope ref can be the unknown one."""
    ts = build_ts_scene(db_session)
    assert client.post(URL, json=ts.body).status_code == 200
    ghost = make(ts.body, target__scope__determination_ref="sdet_" + "c" * 26)
    assert client.post(URL, json=ghost).status_code == 404
    scene = build(db_session)
    ensemble = body_for(
        scene, task="canonical_model", observable="internal_partition",
        target__scope={"kind": "explicit_equilibrium_ensemble", "ensemble_declaration_ref": "smd_" + "c" * 26},
    )
    assert client.post(URL, json=ensemble).status_code == 404


def test_a_release_population_is_the_coded_unsupported_refusal(client, db_session):
    scene = build(db_session)
    response = client.post(URL, json=body_for(scene, population={"kind": "release", "release_ref": "rel_" + "a" * 26}))
    assert response.status_code == 422 and code(response) == "statmech_selection_unsupported"
    assert response.json()["context"]["reason"] == "release_population"


def test_a_bound_exceeded_is_the_coded_422_and_never_a_prefix(client, db_session, monkeypatch):
    scene = build(db_session)
    original = StatmechSelectionRequest.service_request
    monkeypatch.setattr(
        StatmechSelectionRequest, "service_request", lambda self: replace(original(self), bounds=SelectionBounds(candidates=0))
    )
    for suffix in ("", "/manifest"):
        response = client.post(URL + suffix, json=body_for(scene))
        assert response.status_code == 422 and code(response) == "statmech_selection_bound_exceeded"
        assert response.json()["context"]["bound"] == "candidates"
        assert "candidates" not in response.json() and "outcome" not in response.json()  # nothing chosen from a prefix
    monkeypatch.setattr(
        StatmechSelectionRequest, "service_request", lambda self: replace(original(self), bounds=SelectionBounds(manifest_bytes=100))
    )
    response = client.post(URL, json=body_for(scene))
    assert response.status_code == 422 and response.json()["context"]["bound"] == "manifest_bytes"


def test_no_error_body_carries_a_row_id(client, db_session):
    scene = build(db_session)
    for response in (
        client.post(URL, json=body_for(scene, target__species_entry_ref="spe_" + "b" * 26)),
        client.post(URL, json=body_for(scene, population={"kind": "model_revisions", "model_refs": ["smd_" + "b" * 26]})),
    ):
        assert response.status_code == 404
        assert not [k for k in keys(response.json()) if k == "id" or k.endswith("_id")]
