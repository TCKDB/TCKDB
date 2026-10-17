"""The relative conformer energy routes: the HTTP contract.

The service is tested under ``tests/services/relative_conformer_energy``. These tests hold what only the routes can
break: a strict request with no default for the four scientific choices, public refs only, the fixed order of ref
checks, the coded refusals, disclosure under the curated profile, a manifest that downloads and replays, a response
that equals its manifest, and no database id anywhere.
"""

from __future__ import annotations

import json

import pytest

from app.db.models.common import RecordReviewStatus, StructureDeterminationTargetKind, SubmissionRecordType
from app.db.models.level_of_theory import LevelOfTheoryMerge
from app.services.relative_conformer_energy import replay_matches
from tests.services.scientific_read._factories import (
    make_conformer_group,
    make_conformer_observation,
    make_lot,
    set_review,
)
from tests.services.structure_selection._support import make_world
from tests.services.structure_selection.test_service import build_basin

API = "/api/v1/scientific"


def url(entry, *, manifest: bool = False) -> str:
    return f"{API}/species-entries/{entry.public_ref}/conformers/relative-energies" + ("/manifest" if manifest else "")


def conformer(world, energy, *, approve=False, lot=None):
    """A conformer group with one observation and (when ``energy`` is given) one basin determination."""
    group = make_conformer_group(world.session, world.entry)
    obs = make_conformer_observation(world.session, conformer_group=group)
    if energy is not None:
        build_basin(world, energy=energy, observation=obs, lot=lot, target=StructureDeterminationTargetKind.conformer_basin)
    if approve:
        for record_type, row in ((SubmissionRecordType.conformer_group, group), (SubmissionRecordType.conformer_observation, obs)):
            set_review(world.session, record_type=record_type, record_id=row.id, status=RecordReviewStatus.approved)
    world.session.flush()
    return group, obs


def body(world, reference, **changes):
    base = {
        "reference_conformer_ref": reference.public_ref,
        "energy_kind": "electronic_energy",
        "level": {"level_of_theory_ref": world.lot.public_ref},
        "certificate": "local_minimum_any_surface",
    }
    base.update(changes)
    return base


@pytest.fixture
def three(db_session):
    """A reference at -76.400, a conformer 0.001 hartree BELOW it, and one with no determination at all."""
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400)
    low, _ = conformer(world, -76.401)
    bare, _ = conformer(world, None)
    world.settle()
    return world, ref, low, bare


def keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from keys(v)


def code(response):
    out = response.json()
    return out.get("code") if isinstance(out, dict) else None


# ---------------------------------------------------------------------------
# What the route answers
# ---------------------------------------------------------------------------


def test_happy_path_keeps_a_negative_difference_and_reports_the_absent_member_with_its_reason(client, three):
    world, ref, low, bare = three
    response = client.post(url(world.entry), json=body(world, ref))
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["outcome"] == "relative_energies"
    assert out["request"]["entry_ref"] == world.entry.public_ref and out["request"]["profile"] == "exploratory"
    assert out["request"]["level"]["level_of_theory_ref"] == world.lot.public_ref
    by = {m["conformer_ref"]: m for m in out["members"]}
    # Not the lowest reference: the member below it stays negative.
    assert by[low.public_ref]["relative_energy_hartree"] == pytest.approx(-0.001)
    assert by[low.public_ref]["relative_energy_kj_mol"] == pytest.approx(-0.001 * 2625.4996394798254)
    assert by[ref.public_ref]["status"] == "reference" and by[ref.public_ref]["relative_energy_kj_mol"] == 0.0
    # Absent: a coded reason and null numbers, never zero.
    absent = by[bare.public_ref]
    assert absent["status"] == "absent" and absent["absent_reason"] == "no_structure_determination"
    assert absent["relative_energy_kj_mol"] is None and absent["value_hartree"] is None
    # Ordered by ref, with no rank field.
    assert [m["conformer_ref"] for m in out["members"]] == sorted(by)
    assert not any("rank" in k for k in keys(out))
    assert out["units"]["relative"] == "kJ/mol" and out["integrity"]["algorithm"] == "sha256"


def test_the_response_members_equal_the_manifest_result_and_the_manifest_replays(client, three):
    world, ref, *_ = three
    out = client.post(url(world.entry), json=body(world, ref)).json()
    download = client.post(url(world.entry, manifest=True), json=body(world, ref))
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    manifest = download.json()
    assert out["members"] == manifest["result"]["members"]
    assert out["integrity"]["manifest_sha256"] == manifest["integrity"]["manifest_sha256"]
    assert manifest["snapshot_isolation"] != ""  # the harness may opt out of the snapshot; the real-database test pins it
    assert replay_matches(manifest)


def test_a_value_only_at_another_level_is_absent_for_the_requested_one(client, db_session):
    world = make_world(db_session)
    other = make_lot(db_session, method="b3lyp")
    ref, _ = conformer(world, -76.4)
    elsewhere, _ = conformer(world, -76.5, lot=other)
    world.settle()
    out = client.post(url(world.entry), json=body(world, ref)).json()
    by = {m["conformer_ref"]: m for m in out["members"]}
    assert by[elsewhere.public_ref]["absent_reason"] == "no_value_at_requested_level"
    assert by[elsewhere.public_ref]["relative_energy_kj_mol"] is None


def test_a_merged_level_ref_is_answered_under_the_level_it_was_merged_into(client, db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.4)
    other, _ = conformer(world, -76.5)
    duplicate = make_lot(db_session, method="b3lyp")  # no calculation uses it; it is merged into the world's level
    db_session.add(LevelOfTheoryMerge(merged_lot_id=duplicate.id, into_lot_id=world.lot.id))
    db_session.flush()
    world.settle()
    out = client.post(url(world.entry), json=body(world, ref, level={"level_of_theory_ref": duplicate.public_ref})).json()
    assert out["request"]["level"]["level_of_theory_ref"] == world.lot.public_ref
    assert {m["conformer_ref"]: m["status"] for m in out["members"]}[other.public_ref] == "present"


def test_the_legacy_lowest_sp_route_is_deprecated_in_openapi_and_points_to_the_new_operation(client):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/species-entries/{entry_id}/conformer-observations/lowest-sp"]["get"]
    assert operation["deprecated"] is True
    assert "conformers/relative-energies" in operation["description"]
    new = spec["paths"]["/api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies"]["post"]
    assert not new.get("deprecated")


# ---------------------------------------------------------------------------
# A strict request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["energy_kind", "reference_conformer_ref", "level", "certificate"])
def test_each_scientific_choice_is_required_and_the_422_names_it(client, three, missing):
    world, ref, *_ = three
    payload = body(world, ref)
    del payload[missing]
    response = client.post(url(world.entry), json=payload)
    assert response.status_code == 422
    assert missing in json.dumps(response.json())


def test_both_level_refs_or_neither_is_a_422(client, three):
    world, ref, *_ = three
    both = body(world, ref, level={"level_of_theory_ref": world.lot.public_ref, "composite_scheme_ref": "csch_" + "0" * 26})
    neither = body(world, ref, level={})
    assert client.post(url(world.entry), json=both).status_code == 422
    assert client.post(url(world.entry), json=neither).status_code == 422


def test_temperature_with_a_supported_kind_is_a_422(client, three):
    world, ref, *_ = three
    assert client.post(url(world.entry), json=body(world, ref, temperature_k=298.15)).status_code == 422


def test_unknown_body_fields_and_database_ids_are_refused(client, three):
    world, ref, *_ = three
    assert client.post(url(world.entry), json=body(world, ref, species_entry_id=1)).status_code == 422
    assert client.post(url(world.entry), json=body(world, ref, sort="energy")).status_code == 422


def test_query_string_keys_other_than_the_profile_are_refused(client, three):
    world, ref, *_ = three
    for suffix in ("", "/manifest"):
        response = client.post(url(world.entry, manifest=bool(suffix)), json=body(world, ref), params={"energy_kind": "x"})
        assert response.status_code == 422 and "post_search_fields_must_be_in_body" in response.text
    ok = client.post(url(world.entry), json=body(world, ref), params={"profile": "exploratory"})
    assert ok.status_code == 200


def test_an_integer_path_id_is_refused(client, three):
    world, ref, *_ = three
    response = client.post(f"{API}/species-entries/{world.entry.id}/conformers/relative-energies", json=body(world, ref))
    assert response.status_code == 422 and code(response) == "invalid_handle"


# ---------------------------------------------------------------------------
# Coded refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["enthalpy", "gibbs_energy"])
def test_enthalpy_and_gibbs_are_the_coded_unsupported_422(client, three, kind):
    world, ref, *_ = three
    for suffix in ("", "/manifest"):
        response = client.post(
            url(world.entry, manifest=bool(suffix)), json=body(world, ref, energy_kind=kind, temperature_k=298.15)
        )
        assert response.status_code == 422 and code(response) == "relative_conformer_energy_unsupported"
        assert response.json()["context"]["reason"] == "deferred_energy_kind"


def test_a_request_for_no_certificate_is_the_coded_unsupported_422(client, three):
    world, ref, *_ = three
    response = client.post(url(world.entry), json=body(world, ref, certificate="none"))
    assert response.status_code == 422 and code(response) == "relative_conformer_energy_unsupported"
    assert response.json()["context"]["reason"] == "deferred_certificate"


def test_more_than_five_hundred_visible_groups_is_the_coded_bound_and_five_hundred_is_answered(client, db_session):
    world = make_world(db_session)
    groups = [make_conformer_group(db_session, world.entry) for _ in range(501)]
    world.settle()
    refused = client.post(url(world.entry), json=body(world, groups[0]))
    assert refused.status_code == 422 and code(refused) == "relative_conformer_energy_population_too_large"
    assert refused.json()["context"]["visible"] == 501 and refused.json()["context"]["limit"] == 500
    named = [g.public_ref for g in groups[:500]]
    assert client.post(url(world.entry), json=body(world, groups[0], member_refs=named)).status_code == 200


# ---------------------------------------------------------------------------
# Ref checks and hidden-vs-unknown
# ---------------------------------------------------------------------------


def test_a_visible_group_of_another_entry_is_the_coded_owner_mismatch_naming_only_the_field(client, db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.4)
    other = make_world(db_session)
    foreign, _ = conformer(other, -76.4)
    world.settle()
    other.settle()
    as_reference = client.post(url(world.entry), json=body(world, foreign))
    assert as_reference.status_code == 422 and code(as_reference) == "relative_conformer_energy_scope_owner_mismatch"
    assert as_reference.json()["context"] == {"field": "reference_conformer_ref"}
    as_member = client.post(url(world.entry), json=body(world, ref, member_refs=[foreign.public_ref]))
    assert as_member.json()["context"] == {"field": "member_refs"}
    assert foreign.public_ref not in as_member.text and other.entry.public_ref not in as_member.text


def test_an_unknown_ref_is_a_404_and_a_wrong_prefix_is_a_422(client, three):
    world, ref, *_ = three
    ghost = f"cg_{'0' * 26}"
    assert client.post(url(world.entry), json=body(world, ref, reference_conformer_ref=ghost)).status_code == 404
    assert client.post(url(world.entry), json=body(world, ref, member_refs=[ghost])).status_code == 404
    assert client.post(url(world.entry), json=body(world, ref, reference_conformer_ref=world.lot.public_ref)).status_code == 422
    assert client.post(url(world.entry), json=body(world, ref, level={"level_of_theory_ref": f"lot_{'0' * 26}"})).status_code == 404
    assert client.post(url(world.entry), json=body(world, ref, level={"level_of_theory_ref": ref.public_ref})).status_code == 422


def test_a_hidden_ref_is_the_same_404_as_an_unknown_one_for_the_subject_and_the_reference(client, db_session):
    world = make_world(db_session, entry_status=None)
    shown, _ = conformer(world, -76.4, approve=True)
    world.settle()
    ghost_entry = f"spe_{'0' * 26}"
    payload = body(world, shown)
    hidden = client.post(url(world.entry), json=payload, params={"profile": "curated"})
    missing = client.post(f"{API}/species-entries/{ghost_entry}/conformers/relative-energies", json=payload, params={"profile": "curated"})
    assert hidden.status_code == missing.status_code == 404 and code(hidden) == "handle_not_found"
    assert json.loads(hidden.text.replace(world.entry.public_ref, "<ref>")) == json.loads(missing.text.replace(ghost_entry, "<ref>"))

    visible = make_world(db_session)
    approved, _ = conformer(visible, -76.4, approve=True)
    unreviewed, _ = conformer(visible, -76.5)  # no review: curated hides it
    visible.settle()
    ghost = f"cg_{'0' * 26}"
    hidden_ref = client.post(url(visible.entry), json=body(visible, unreviewed), params={"profile": "curated"})
    unknown_ref = client.post(url(visible.entry), json=body(visible, approved, reference_conformer_ref=ghost), params={"profile": "curated"})
    assert hidden_ref.status_code == unknown_ref.status_code == 404
    assert json.loads(hidden_ref.text.replace(unreviewed.public_ref, "<ref>")) == json.loads(unknown_ref.text.replace(ghost, "<ref>"))
    as_member = client.post(url(visible.entry), json=body(visible, approved, member_refs=[unreviewed.public_ref]), params={"profile": "curated"})
    assert as_member.status_code == 404


def test_a_hidden_group_of_another_entry_is_a_404_not_an_owner_mismatch_under_curated(client, db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.4, approve=True)
    other = make_world(db_session)
    foreign, _ = conformer(other, -76.4)  # unreviewed: hidden under curated
    world.settle()
    other.settle()
    response = client.post(url(world.entry), json=body(world, ref, member_refs=[foreign.public_ref]), params={"profile": "curated"})
    assert response.status_code == 404 and code(response) == "handle_not_found"


# ---------------------------------------------------------------------------
# Disclosure under the curated profile
# ---------------------------------------------------------------------------


def test_curated_names_no_hidden_group_observation_determination_or_calculation(client, db_session):
    world = make_world(db_session)
    shown, shown_obs = conformer(world, -76.400, approve=True)
    shown_calcs = {c.public_ref for c in world.calcs.values()}
    hidden, hidden_obs = conformer(world, -76.410)  # its own group has no review: curated hides it
    set_review(
        db_session, record_type=SubmissionRecordType.conformer_observation, record_id=hidden_obs.id, status=RecordReviewStatus.approved
    )
    world.settle()
    hidden_calcs = {c.public_ref for c in world.calcs.values()} - shown_calcs
    assert shown_calcs and hidden_calcs
    out = client.post(url(world.entry), json=body(world, shown), params={"profile": "curated"})
    manifest = client.post(url(world.entry, manifest=True), json=body(world, shown), params={"profile": "curated"})
    assert out.status_code == manifest.status_code == 200
    assert out.json()["request"]["profile"] == "curated"
    assert [m["conformer_ref"] for m in out.json()["members"]] == [shown.public_ref]
    assert out.json()["coverage"]["requested_members"] == 1
    for doc in (out.text, manifest.text):
        assert hidden.public_ref not in doc and hidden_obs.public_ref not in doc
    # Every calculation the hidden group's determination pinned stays out; the shown group's are named.
    assert any(r in manifest.text for r in shown_calcs)
    assert not any(r in out.text or r in manifest.text for r in hidden_calcs)
    assert replay_matches(manifest.json())
    exploratory = client.post(url(world.entry), json=body(world, shown)).json()
    assert {m["conformer_ref"] for m in exploratory["members"]} == {shown.public_ref, hidden.public_ref}


# ---------------------------------------------------------------------------
# No database id leaves the building
# ---------------------------------------------------------------------------


def test_no_database_id_appears_in_either_document(client, three):
    world, ref, *_ = three
    out = client.post(url(world.entry), json=body(world, ref)).json()
    manifest = client.post(url(world.entry, manifest=True), json=body(world, ref)).json()
    for document in (out, manifest):
        found = {k for k in keys(document) if k == "id" or (k.endswith("_id") and k not in {"cohort_id", "recipe_cohort_id", "rule_id"})}
        assert found == set(), found
        text = json.dumps(document)
        assert "calculation_id" not in text and "species_entry_id" not in text
