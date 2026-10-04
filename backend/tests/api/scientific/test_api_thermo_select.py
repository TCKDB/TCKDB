"""``POST /scientific/species-entries/{ref}/thermo/select``: the public H298 selection endpoint.

The engine, rules and manifest are tested under ``tests/services/thermo_selection``. These
tests hold the HTTP contract: the request is normalised or refused, every outcome is reachable,
the response says why, visibility follows the read profile, the manifest replays, and no
database id leaves the building.
"""

from __future__ import annotations

import json

import pytest

from app.db.models.common import (
    EnthalpyReferenceKind,
    PhaseKind,
    ScientificOriginKind,
    SubmissionRecordType,
    ThermoTargetKind,
)
from app.db.models.common import RecordReviewStatus as S
from app.db.models.species import Species
from app.db.models.thermo import Thermo
from app.schemas.reads.scientific_thermo_selection import ThermoSelectionOutcome
from app.services.thermo_selection import Outcome, replay_decision, replay_matches
from tests.services.scientific_read._factories import (
    make_conformer_group,
    make_species,
    make_species_entry,
    set_review,
)
from tests.services.thermo_selection._support import T0, make_thermo, protocol, species_entry_for
from tests.services.thermo_selection.test_engine import LabelRule

EQUILIBRIUM = {"target": {"kind": "equilibrium_ensemble"}}


def select_url(entry, *, manifest: bool = False) -> str:
    return f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo/select" + ("/manifest" if manifest else "")


def post(client, entry, body=None, *, profile: str | None = None, manifest: bool = False):
    params = {"profile": profile} if profile else None
    return client.post(select_url(entry, manifest=manifest), json=EQUILIBRIUM if body is None else body, params=params)


@pytest.fixture
def methane(db_session):
    return species_entry_for(db_session, "Methane")


def g4(session, entry, **kw):
    kw.setdefault("proto", protocol("g4"))
    return make_thermo(session, entry, **kw)


def g3(session, entry, **kw):
    kw.setdefault("proto", protocol("g3"))
    return make_thermo(session, entry, **kw)


def code(response) -> str:
    return response.json()["code"]


# -- the headline: scientific preference outranks recency, end to end ------------------------------------


def test_an_older_qualifying_g4_is_selected_over_a_newer_g3_while_browse_still_lists_g3_first(
    client, db_session, methane
):
    old_g4 = g4(db_session, methane, age_days=500, status=S.approved)
    new_g3 = g3(db_session, methane, age_days=1, status=S.approved)

    browse = client.get(f"/api/v1/scientific/species-entries/{methane.public_ref}/thermo", params={"collapse": "first"})
    assert browse.status_code == 200
    assert browse.json()["records"][0]["thermo_ref"] == new_g3.public_ref  # browse order is unchanged

    response = post(client, methane)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "policy_preferred"
    assert body["selection"] == {
        "thermo_ref": old_g4.public_ref,
        "basis": "policy_preferred",
        "administrative": False,
        "explanation": body["basis"],
    }
    assert body["fronts"] == [[old_g4.public_ref], [new_g3.public_ref]]
    assert body["administrative_order"][0] == new_g3.public_ref  # administrative order alone says G3
    assert body["relations"]["edges"] == [
        {"preferred": old_g4.public_ref, "dispreferred": new_g3.public_ref, "rule_id": "E1", "rule_version": "1.0.0"}
    ]
    assert body["policy"]["rules"][0]["rule_id"] == "E1"
    assert body["policy"]["name"] == "h298_method_preferred" and body["policy"]["version"] == "1"


def test_the_response_echoes_the_normalised_request_and_the_effective_floor(client, db_session, methane):
    g4(db_session, methane, status=S.approved)
    body = post(client, methane, {"target": {"kind": "equilibrium_ensemble"}, "temperature_k": 298.15, "phase": "gas"}).json()
    assert body["request"]["species_entry_ref"] == methane.public_ref
    assert body["request"]["quantity"] == "formation_enthalpy_298k"
    assert body["request"]["temperature_k"] == 298.15 and body["request"]["phase"] == "gas"
    assert body["request"]["policy"] == "method_preferred" and body["request"]["result_mode"] == "all"
    assert body["request"]["target"] == {"kind": "equilibrium_ensemble", "conformer_group_ref": None}
    assert body["request"]["profile"] == "exploratory"
    assert body["review"]["effective_floor"] == "not_reviewed"
    assert body["review"]["effective_statuses"] == ["approved", "under_review", "not_reviewed"]


# -- every outcome is reachable over HTTP -----------------------------------------------------------------


def test_incomparable_alternatives(client, db_session, methane):
    a = g3(db_session, methane, age_days=3)
    b = g3(db_session, methane, age_days=1)
    body = post(client, methane).json()
    assert body["outcome"] == "incomparable_alternatives"
    assert body["selection"] is None
    assert set(body["fronts"][0]) == {a.public_ref, b.public_ref}


def test_sole_eligible_candidate(client, db_session, methane):
    only = g4(db_session, methane)
    body = post(client, methane).json()
    assert body["outcome"] == "sole_eligible_candidate"
    assert body["selection"]["thermo_ref"] == only.public_ref and body["selection"]["administrative"] is False


def test_no_applicable_candidate_when_there_are_no_records(client, methane):
    body = post(client, methane).json()
    assert body["outcome"] == "no_applicable_candidate"
    assert body["selection"] is None and body["candidates"] == []


def test_no_applicable_candidate_when_every_record_is_inapplicable_and_is_listed_as_unresolved(
    client, db_session, methane
):
    undeclared = make_thermo(db_session, methane, target=None)
    body = post(client, methane).json()
    assert body["outcome"] == "no_applicable_candidate"
    assert body["disclosures"]["unresolved_refs"] == [undeclared.public_ref]
    assert body["candidates"][0]["applicability"] == "unresolved" and body["candidates"][0]["eligible"] is False


def test_policy_conflict(client, db_session, methane, monkeypatch):
    a = g4(db_session, methane, proto=protocol("g4", label="a"), age_days=5)
    b = g4(db_session, methane, proto=protocol("g4", label="b"), age_days=1)
    rules = (LabelRule("T1", {"a"}, {"b"}), LabelRule("T2", {"b"}, {"a"}))
    monkeypatch.setattr("app.services.thermo_selection.service.default_rules", lambda: rules)
    body = post(client, methane).json()
    assert body["outcome"] == "policy_conflict"
    assert body["selection"] is None
    assert body["relations"]["opposing_pairs"]
    assert {a.public_ref, b.public_ref} == set(body["administrative_order"])


def _bulk(session, entry, n, *, offset=0):
    rows = [
        Thermo(
            species_entry_id=entry.id, scientific_origin=ScientificOriginKind.computed,
            h298_kj_mol=-74.6 - (offset + i) * 1e-3, enthalpy_reference_kind=EnthalpyReferenceKind.formation_298k,
            phase=PhaseKind.gas, thermodynamic_target_kind=ThermoTargetKind.equilibrium_ensemble, created_at=T0,
        )
        for i in range(n)
    ]
    session.add_all(rows)
    session.flush()
    return rows


def test_more_than_the_cap_of_visible_records_is_a_coded_refusal_not_an_outcome(client, db_session, methane):
    _bulk(db_session, methane, 501)
    response = post(client, methane)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "thermo_selection_population_too_large"
    assert body["context"] == {"limit": 500, "visible_candidates": 501}
    assert post(client, methane, manifest=True).status_code == 422


def test_exactly_the_cap_is_still_assessed(client, db_session, methane):
    _bulk(db_session, methane, 500)
    response = post(client, methane)
    assert response.status_code == 200
    assert response.json()["disclosures"]["visible_candidates"] == 500


def test_the_cap_counts_only_records_visible_to_the_caller(client, db_session, methane):
    visible = _bulk(db_session, methane, 101)
    for row in visible:
        set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=row.id, status=S.approved)
    _bulk(db_session, methane, 400, offset=101)  # 400 never-reviewed records: hidden under the curated profile
    curated = post(client, methane, profile="curated")
    assert curated.status_code == 200, curated.text
    assert curated.json()["disclosures"]["visible_candidates"] == 101
    assert "bounded" not in curated.text and "population_too_large" not in curated.text
    assert post(client, methane, profile="curated", manifest=True).status_code == 200
    exploratory = post(client, methane)  # the same entry, a profile that can see all 501
    assert exploratory.status_code == 422
    assert exploratory.json()["context"]["visible_candidates"] == 501


def test_the_refusal_reports_the_visible_count_not_the_total_under_curated(client, db_session, methane):
    for row in _bulk(db_session, methane, 501):
        set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=row.id, status=S.approved)
    _bulk(db_session, methane, 50, offset=501)  # hidden under curated
    response = post(client, methane, profile="curated")
    assert response.status_code == 422
    assert response.json()["context"] == {"limit": 500, "visible_candidates": 501}


def test_the_outcome_vocabulary_is_the_services_vocabulary_without_the_refusal():
    assert {o.value for o in ThermoSelectionOutcome} == {o.value for o in Outcome} - {"bounded_search_exceeded"}


# -- result mode: an administrative pick says so ---------------------------------------------------------


def test_first_picks_administratively_among_unranked_alternatives_and_says_so(client, db_session, methane):
    older = g3(db_session, methane, age_days=3, status=S.approved)
    newer = g3(db_session, methane, age_days=1, status=S.approved)
    first = post(client, methane, {**EQUILIBRIUM, "result_mode": "first"}).json()
    assert first["outcome"] == "incomparable_alternatives"
    assert first["selection"]["thermo_ref"] == newer.public_ref
    assert first["selection"]["basis"] == "administrative_first" and first["selection"]["administrative"] is True
    assert "not a claim" in first["selection"]["explanation"]
    assert older.public_ref in first["fronts"][0]

    every = post(client, methane).json()  # mode "all" never makes the administrative pick for the caller
    assert every["selection"] is None


def test_first_does_not_relabel_a_policy_preferred_result_as_administrative(client, db_session, methane):
    old_g4 = g4(db_session, methane, age_days=500)
    g3(db_session, methane, age_days=1)
    body = post(client, methane, {**EQUILIBRIUM, "result_mode": "first"}).json()
    assert body["selection"]["thermo_ref"] == old_g4.public_ref
    assert body["selection"]["basis"] == "policy_preferred" and body["selection"]["administrative"] is False


@pytest.mark.parametrize("policy", ["default", "most_reviewed", "latest"])
def test_administrative_policies_apply_no_method_rule(client, db_session, methane, policy):
    g4(db_session, methane, age_days=500)
    g3(db_session, methane, age_days=1)
    body = post(client, methane, {**EQUILIBRIUM, "policy": policy}).json()
    assert body["outcome"] == "incomparable_alternatives"
    assert body["relations"]["edges"] == [] and body["policy"]["rules"] == []


def test_latest_and_most_reviewed_order_differently(client, db_session, methane):
    approved_old = g3(db_session, methane, age_days=9, status=S.approved)
    fresh = g3(db_session, methane, age_days=1, status=S.not_reviewed)
    latest = post(client, methane, {**EQUILIBRIUM, "policy": "latest", "result_mode": "first"}).json()
    reviewed = post(client, methane, {**EQUILIBRIUM, "policy": "most_reviewed", "result_mode": "first"}).json()
    assert latest["selection"]["thermo_ref"] == fresh.public_ref
    assert reviewed["selection"]["thermo_ref"] == approved_old.public_ref


# -- refusals ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "field"),
    [({"temperature_k": 300.0}, "temperature_k"), ({"phase": "aqueous"}, "phase")],
)
def test_a_conflicting_temperature_or_phase_is_refused_not_normalised(client, methane, extra, field):
    response = post(client, methane, {**EQUILIBRIUM, **extra})
    assert response.status_code == 422
    assert code(response) == "thermo_selection_condition_conflict"
    assert response.json()["context"]["field"] == field


def test_another_quantity_is_refused(client, methane):
    assert post(client, methane, {**EQUILIBRIUM, "quantity": "heat_capacity"}).status_code == 422


def test_single_conformer_without_a_group_is_refused(client, methane):
    response = post(client, methane, {"target": {"kind": "single_conformer"}})
    assert response.status_code == 422 and code(response) == "thermo_target_group_required"


def test_equilibrium_with_a_group_is_refused(client, db_session, methane):
    group = make_conformer_group(db_session, methane)
    response = post(client, methane, {"target": {"kind": "equilibrium_ensemble", "conformer_group_ref": group.public_ref}})
    assert response.status_code == 422 and code(response) == "thermo_target_group_not_allowed"


def test_the_target_is_required(client, methane):
    assert post(client, methane, {}).status_code == 422


def test_the_candidate_cap_is_not_a_request_field(client, methane):
    response = post(client, methane, {**EQUILIBRIUM, "max_candidates": 5})
    assert response.status_code == 422


def test_an_integer_id_is_not_a_species_entry_handle(client, methane):
    response = client.post(
        f"/api/v1/scientific/species-entries/{methane.id}/thermo/select", json=EQUILIBRIUM
    )
    assert response.status_code == 422 and code(response) == "invalid_handle"


def test_a_wrong_prefix_ref_is_refused(client, db_session, methane):
    group = make_conformer_group(db_session, methane)
    response = client.post(
        f"/api/v1/scientific/species-entries/{group.public_ref}/thermo/select", json=EQUILIBRIUM
    )
    assert response.status_code == 422 and code(response) == "handle_type_mismatch"


def test_an_unknown_entry_is_404(client):
    response = client.post("/api/v1/scientific/species-entries/spe_doesnotexist0/thermo/select", json=EQUILIBRIUM)
    assert response.status_code == 404


def test_query_string_fields_are_refused(client, methane):
    response = client.post(select_url(methane), json=EQUILIBRIUM, params={"policy": "latest"})
    assert response.status_code == 422


def test_the_endpoint_is_read_only(client, db_session, methane):
    g4(db_session, methane)
    before = db_session.query(Thermo).count()
    post(client, methane)
    post(client, methane, manifest=True)
    assert db_session.query(Thermo).count() == before


# -- single_conformer ------------------------------------------------------------------------------------


def test_single_conformer_and_equilibrium_targets_stay_distinct(client, db_session, methane):
    group = make_conformer_group(db_session, methane)
    one = g4(db_session, methane, target=ThermoTargetKind.single_conformer, group_id=group.id)
    eq = g4(db_session, methane)
    single = post(client, methane, {"target": {"kind": "single_conformer", "conformer_group_ref": group.public_ref}})
    assert single.status_code == 200, single.text
    assert single.json()["selection"]["thermo_ref"] == one.public_ref
    assert single.json()["request"]["target"] == {"kind": "single_conformer", "conformer_group_ref": group.public_ref}
    assert post(client, methane).json()["selection"]["thermo_ref"] == eq.public_ref


def test_a_group_of_another_entry_is_refused(client, db_session, methane):
    other = make_species_entry(db_session, make_species(db_session, smiles="CC", inchi_key="OTHERKEYAAAAAA-UHFFFAOYSA-N"))
    group = make_conformer_group(db_session, other)
    response = post(client, methane, {"target": {"kind": "single_conformer", "conformer_group_ref": group.public_ref}})
    assert response.status_code == 422 and code(response) == "thermo_target_group_owner_mismatch"


def test_an_unknown_group_ref_is_404(client, methane):
    response = post(client, methane, {"target": {"kind": "single_conformer", "conformer_group_ref": "cg_doesnotexist0"}})
    assert response.status_code == 404


# -- review floor and visibility -------------------------------------------------------------------------


def test_the_callers_floor_changes_eligibility_and_lists_what_it_excluded(client, db_session, methane):
    approved = g3(db_session, methane, status=S.approved)
    pending = g4(db_session, methane, status=S.under_review, age_days=400)
    body = post(client, methane, {**EQUILIBRIUM, "min_review_status": "approved"}).json()
    assert body["review"]["effective_floor"] == "approved"
    assert body["outcome"] == "sole_eligible_candidate" and body["selection"]["thermo_ref"] == approved.public_ref
    assert body["disclosures"]["excluded_by_review"] == [
        {"thermo_ref": pending.public_ref, "review_status": "under_review", "reason": "below_review_floor"}
    ]
    assert body["disclosures"]["excluded_by_review_withheld"] is False
    # Without the floor the older G4 wins: the floor, not the timestamp, decided the result above.
    assert post(client, methane).json()["selection"]["thermo_ref"] == pending.public_ref


def test_rejected_and_deprecated_never_compete_and_are_listed_when_the_profile_has_no_floor(
    client, db_session, methane
):
    rejected = g4(db_session, methane, status=S.rejected, age_days=400)
    g3(db_session, methane, status=S.approved)
    body = post(client, methane).json()
    assert body["outcome"] == "sole_eligible_candidate"
    assert [e["thermo_ref"] for e in body["disclosures"]["excluded_by_review"]] == [rejected.public_ref]


def test_under_the_curated_profile_records_below_the_floor_are_neither_listed_nor_countable(
    client, db_session, methane
):
    approved = g3(db_session, methane, status=S.approved)
    hidden_pending = g4(db_session, methane, status=S.under_review, age_days=400)
    hidden_unreviewed = g4(db_session, methane, age_days=300)
    hidden_rejected = g4(db_session, methane, status=S.rejected, age_days=200)

    curated = post(client, methane, profile="curated")
    assert curated.status_code == 200
    body = curated.json()
    assert body["request"]["profile"] == "curated"
    assert body["review"]["effective_floor"] == "approved"
    assert body["outcome"] == "sole_eligible_candidate" and body["selection"]["thermo_ref"] == approved.public_ref
    assert body["disclosures"]["excluded_by_review"] == [] and body["disclosures"]["excluded_by_review_withheld"] is True
    assert body["disclosures"]["visible_candidates"] == 1
    hidden = {hidden_pending.public_ref, hidden_unreviewed.public_ref, hidden_rejected.public_ref}
    assert not any(ref in curated.text for ref in hidden)

    manifest = post(client, methane, profile="curated", manifest=True)
    assert not any(ref in manifest.text for ref in hidden)
    population = manifest.json()["population"]
    assert population["thermo_rows_for_entry"] == population["visible_candidates"] == 1
    assert population["excluded_by_review_withheld"] is True

    exploratory = post(client, methane)
    assert {e["thermo_ref"] for e in exploratory.json()["disclosures"]["excluded_by_review"]} == {hidden_rejected.public_ref}


def test_a_curated_caller_asking_for_a_weaker_floor_still_gets_the_curated_one(client, db_session, methane):
    g3(db_session, methane, status=S.approved)
    g4(db_session, methane, status=S.not_reviewed, age_days=400)
    body = post(client, methane, {**EQUILIBRIUM, "min_review_status": "not_reviewed"}, profile="curated").json()
    assert body["review"]["effective_floor"] == "approved"
    assert body["outcome"] == "sole_eligible_candidate"


# -- the manifest ----------------------------------------------------------------------------------------


def test_the_manifest_download_replays_to_the_decision_it_records(client, db_session, methane):
    g4(db_session, methane, age_days=500, status=S.approved)
    g3(db_session, methane, age_days=1, status=S.approved)
    response = post(client, methane, manifest=True)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    manifest = response.json()
    assert manifest["outcome"] == "policy_preferred"
    assert replay_matches(manifest)
    assert replay_decision(manifest)["selected_ref"] == manifest["decision"]["selected_ref"]
    # The response and the download are one decision.
    body = post(client, methane).json()
    assert body["selection"]["thermo_ref"] == manifest["decision"]["selected_ref"]
    assert body["fronts"] == manifest["decision"]["fronts"]


def test_a_manifest_for_an_administrative_policy_replays_too(client, db_session, methane):
    g4(db_session, methane, age_days=500)
    g3(db_session, methane, age_days=1)
    manifest = post(client, methane, {**EQUILIBRIUM, "policy": "latest"}, manifest=True).json()
    assert manifest["decision"]["rules"] == [] and replay_matches(manifest)


def test_a_curated_manifest_still_replays(client, db_session, methane):
    g4(db_session, methane, age_days=500, status=S.approved)
    g3(db_session, methane, age_days=1, status=S.approved)
    g3(db_session, methane, age_days=1)
    manifest = post(client, methane, profile="curated", manifest=True).json()
    assert replay_matches(manifest)


# -- no internal ids -------------------------------------------------------------------------------------

#: Registry keys (``E1``), not row ids.
_ALLOWED_ID_SHAPED_KEYS = {"rule_id", "overridden_by_rule_id"}


_ALLOWED_REF_PREFIXES = {"thm", "spe", "cg", "calc"}


def _keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _id_shaped(keys):
    return sorted({k for k in keys if (k == "id" or k.endswith(("_id", "_ids"))) and k not in _ALLOWED_ID_SHAPED_KEYS})


def _int_paths(value, path=""):
    """Every integer in a document with its path (list indices written ``[]``), booleans excluded."""
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _int_paths(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for item in value:
            yield from _int_paths(item, f"{path}[]")


def _assert_integers_are_pinned(doc, *, is_manifest, curated):
    """Each integer sits at an exact path and has the value that path is allowed to have.

    A name-based allowance would let a row id ride under an innocent key (``version``) anywhere in the
    document; here a path outside this table fails, and so does a value that is not what the path means.
    """
    candidates = doc["candidates"]
    pinned = {"candidates[].protocol.version": lambda values: set(values) == {1}}
    if is_manifest:
        population = doc["population"]
        pinned.update({
            "manifest_format_version": lambda v: v == [1],
            "candidates[].id_rank": lambda v: sorted(v) == list(range(1, len(candidates) + 1)),
            "population.visible_candidates": lambda v: v == [len(candidates)],
            "population.assessed": lambda v: v == [len(candidates)],
            "population.thermo_rows_for_entry": lambda v: (
                v == [len(candidates)] if curated else v[0] >= len(candidates)
            ),
            "subject.charge": lambda v: v == [0],
            "subject.multiplicity": lambda v: v == [1],
        })
        assert population["visible_candidates"] == len(candidates)
    else:
        pinned["disclosures.visible_candidates"] = lambda v: v == [len(candidates)]
    found: dict[str, list[int]] = {}
    for path, value in _int_paths(doc):
        found.setdefault(path, []).append(value)
    stray = sorted(set(found) - set(pinned))
    assert stray == [], f"integers at unexpected paths: {stray}"
    wrong = {path: values for path, values in found.items() if not pinned[path](values)}
    assert wrong == {}, f"integers with values their paths do not allow: {wrong}"


def _ref_values(value):
    """Every string value that sits under a ``*_ref`` / ``*_refs`` key (or in a list of them)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k.endswith(("_ref", "_refs")) or k in {"preferred", "dispreferred"}:
                yield from ([v] if isinstance(v, str) else v if isinstance(v, list) else [])
            yield from _ref_values(v)
    elif isinstance(value, list):
        for item in value:
            yield from _ref_values(item)


def test_no_internal_id_appears_in_the_response_or_the_manifest(client, db_session, methane, monkeypatch):
    group = make_conformer_group(db_session, methane)
    g4(db_session, methane, age_days=500, status=S.approved)
    g3(db_session, methane, age_days=1, status=S.approved)
    g3(db_session, methane, target=ThermoTargetKind.single_conformer, group_id=group.id)
    make_thermo(db_session, methane, target=None)
    g4(db_session, methane, status=S.rejected)
    with_calc = {**protocol("g4"), "supporting_calculations": [{"calculation_ref": "calc_0123456789ab"}]}
    make_thermo(db_session, methane, proto=with_calc, age_days=2, status=S.approved)
    # Two rules, one superseding the other, so the decision carries an overridden edge.
    a = make_thermo(db_session, methane, proto=protocol("g4", label="a"), age_days=5, status=S.approved)
    b = make_thermo(db_session, methane, proto=protocol("g4", label="b"), age_days=4, status=S.approved)
    rules = (LabelRule("S1", {"a"}, {"b"}, supersedes=("S2",)), LabelRule("S2", {"b"}, {"a"}))
    monkeypatch.setattr("app.services.thermo_selection.service.default_rules", lambda: rules)

    docs = [
        post(client, methane).json(),
        post(client, methane, manifest=True).json(),
        post(client, methane, profile="curated").json(),
        post(client, methane, profile="curated", manifest=True).json(),
        post(client, methane, {"target": {"kind": "single_conformer", "conformer_group_ref": group.public_ref}}).json(),
    ]
    assert docs[0]["relations"]["overridden_edges"], "the fixture no longer produces an overridden edge"
    assert "overridden_by_rule_id" in json.dumps(docs[0]["relations"]["overridden_edges"])
    assert any(
        c["protocol"] and c["protocol"].get("supporting_calculations") for c in docs[0]["candidates"]
    ), "the fixture no longer carries supporting calculations"
    assert a.public_ref in json.dumps(docs[0]) and b.public_ref in json.dumps(docs[0])
    for doc in docs:
        assert _id_shaped(_keys(doc)) == []
        _assert_integers_are_pinned(doc, is_manifest="population" in doc, curated=doc["request"]["profile"] == "curated")
        refs = {r for r in _ref_values(doc) if isinstance(r, str)}
        assert refs, "the document names no refs at all; the check below would be vacuous"
        assert {r.split("_", 1)[0] for r in refs} <= _ALLOWED_REF_PREFIXES, refs


def test_candidate_refs_are_thermo_public_refs(client, db_session, methane):
    row = g4(db_session, methane)
    body = post(client, methane).json()
    assert body["candidates"][0]["thermo_ref"] == row.public_ref
    assert row.public_ref.startswith("thm_")


# -- non-finite temperatures -----------------------------------------------------------------------------


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_temperature_is_a_clean_422_not_a_500(client, methane, literal):
    response = client.post(
        select_url(methane),
        content=f'{{"target": {{"kind": "equilibrium_ensemble"}}, "temperature_k": {literal}}}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    # Strict JSON (no NaN or Infinity tokens in the reply), and the refusal is the request-validation one,
    # not the condition-conflict refusal that would have to echo the non-finite value back.
    body = json.loads(response.text, parse_constant=lambda token: pytest.fail(f"{token} in the response"))
    assert body["code"] != "thermo_selection_condition_conflict"
    assert "finite number" in json.dumps(body["detail"]), body  # named for what it is, not a serializer failure


# -- the curated profile is not an oracle for hidden records --------------------------------------------


def _build(session, entry, *, with_hidden: bool):
    """The same visible records, optionally with hidden ones interleaved by id. Returns ref -> label."""
    labels: dict[str, str] = {}
    plan = [
        ("old_g4", "g4", 500, S.approved),
        ("new_g3", "g3", 1, S.approved),
        ("mid_g3", "g3", 3, S.approved),
        ("sole_other", "g3", 7, S.approved),
    ]
    hidden = [
        ("h_review", "g4", 900, S.under_review, {}),
        ("h_unrev", "g3", 2, S.not_reviewed, {}),
        ("h_rej", "g4", 800, S.rejected, {}),
        ("h_dep", "g4", 700, S.deprecated, {}),
        ("h_unresolved", "g4", 5, S.not_reviewed, {"target": None}),
    ]
    for index, (label, recipe, age, status) in enumerate(plan):
        if with_hidden:
            hlabel, hrecipe, hage, hstatus, extra = hidden[index % len(hidden)]
            make_thermo(session, entry, proto=protocol(hrecipe), age_days=hage, status=hstatus, **extra)
            hlabel, hrecipe, hage, hstatus, extra = hidden[(index + 2) % len(hidden)]
            make_thermo(session, entry, proto=protocol(hrecipe), age_days=hage, status=hstatus, **extra)
        row = make_thermo(session, entry, proto=protocol(recipe), age_days=age, status=status)
        labels[row.public_ref] = label
    if with_hidden:
        for _label, hrecipe, hage, hstatus, extra in hidden:
            make_thermo(session, entry, proto=protocol(hrecipe), age_days=hage, status=hstatus, **extra)
    return labels


def _canonical(value):
    """Lists of objects are sorted: some are ordered by the ref string itself (edges, per-rule candidate
    lists), and two entries' refs sort differently whatever else they hold. Lists of strings keep their order,
    so ``fronts`` and ``administrative_order`` are still compared as ordered."""
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        items = [_canonical(v) for v in value]
        if items and all(isinstance(v, dict) for v in items):
            return sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
        return items
    return value


def _masked(doc, labels, entry_ref):
    text = json.dumps(doc, sort_keys=True)
    for ref, label in labels.items():
        text = text.replace(ref, label)
    return _canonical(json.loads(text.replace(entry_ref, "ENTRY")))


def test_curated_output_is_identical_with_and_without_hidden_records(client, db_session, methane):
    species = db_session.get(Species, methane.species_id)
    twin = make_species_entry(db_session, species, term_symbol="TWIN")
    assert twin.id != methane.id
    plain_labels = _build(db_session, methane, with_hidden=False)
    noisy_labels = _build(db_session, twin, with_hidden=True)
    assert sorted(plain_labels.values()) == sorted(noisy_labels.values())

    for manifest in (False, True):
        plain = post(client, methane, profile="curated", manifest=manifest)
        noisy = post(client, twin, profile="curated", manifest=manifest)
        assert plain.status_code == noisy.status_code == 200
        assert _masked(noisy.json(), noisy_labels, twin.public_ref) == _masked(
            plain.json(), plain_labels, methane.public_ref
        ), f"curated {'manifest' if manifest else 'response'} depends on records the profile hides"

    # The comparison is not vacuous: without the curated floor the hidden records are in play.
    exploratory = post(client, twin, profile=None).json()
    assert exploratory["disclosures"]["visible_candidates"] > 4


def test_manifest_id_rank_is_an_ordinal_over_visible_records_not_a_row_id(client, db_session, methane):
    _build(db_session, methane, with_hidden=True)
    manifest = post(client, methane, profile="curated", manifest=True).json()
    ranks = sorted(c["id_rank"] for c in manifest["candidates"])
    assert ranks == list(range(1, len(ranks) + 1))
    assert len(ranks) == 4
    exploratory = post(client, methane, manifest=True).json()
    all_ranks = sorted(c["id_rank"] for c in exploratory["candidates"])
    assert all_ranks == list(range(1, len(all_ranks) + 1))


def test_withheld_is_true_under_curated_even_when_nothing_is_hidden(client, db_session, methane):
    g4(db_session, methane, status=S.approved)
    curated = post(client, methane, profile="curated").json()
    assert curated["disclosures"]["excluded_by_review_withheld"] is True
    assert curated["disclosures"]["excluded_by_review"] == []
    exploratory = post(client, methane).json()
    assert exploratory["disclosures"]["excluded_by_review_withheld"] is False
    manifest = post(client, methane, profile="curated", manifest=True).json()
    assert manifest["population"]["excluded_by_review_withheld"] is True


def test_the_downloadable_manifest_does_not_carry_the_candidate_cap(client, db_session, methane):
    g4(db_session, methane)
    manifest = post(client, methane, manifest=True).json()
    assert "max_candidates" not in json.dumps(manifest)
    assert replay_matches(manifest)


def test_the_manifest_route_declares_its_own_response_schema(client):
    spec = client.get("/openapi.json")
    if spec.status_code != 200:  # hosted posture: the document is not served
        pytest.skip("OpenAPI document not exposed in this configuration")
    paths = spec.json()["paths"]
    base = "/api/v1/scientific/species-entries/{species_entry_ref}/thermo/select"

    def schema_ref(path: str) -> str:
        return paths[path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]

    assert schema_ref(base).endswith("/ThermoSelectionResponse")
    assert schema_ref(base + "/manifest").endswith("/ThermoSelectionManifest")


def test_the_manifest_records_the_profile_it_was_made_under(client, db_session, methane):
    g4(db_session, methane, status=S.approved)
    exploratory = post(client, methane, manifest=True).json()["request"]
    curated = post(client, methane, profile="curated", manifest=True).json()["request"]
    assert exploratory["profile"] == "exploratory" and curated["profile"] == "curated"
    assert curated["profile_recommendation"] == "approved_floor_only"


def test_real_manifests_validate_against_the_declared_schema_and_it_names_every_key(client, db_session, methane):
    """The manifest route returns a raw ``JSONResponse``, so its ``response_model`` is never enforced at
    runtime. Validating real documents here is what keeps the declared schema and the document in step."""
    from app.schemas.reads.scientific_thermo_selection import ThermoSelectionManifest

    g4(db_session, methane, age_days=500, status=S.approved)
    g3(db_session, methane, age_days=1, status=S.approved)
    g3(db_session, methane, status=S.not_reviewed)
    g4(db_session, methane, status=S.rejected)
    for profile in (None, "curated"):
        document = post(client, methane, profile=profile, manifest=True).json()
        model = ThermoSelectionManifest.model_validate(document)
        assert set(document) == set(ThermoSelectionManifest.model_fields), "the document and the schema disagree on keys"
        assert model.outcome.value == document["outcome"]
        assert model.request.profile.value == (profile or "exploratory")
        assert len(model.candidates) == len(document["candidates"])
