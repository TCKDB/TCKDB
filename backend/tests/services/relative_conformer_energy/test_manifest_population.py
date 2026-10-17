"""The population claims in a manifest: replay through them, and refusal of a population that contradicts itself."""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.services.relative_conformer_energy import replay_matches, replay_relative_energies
from app.services.structure_selection.manifest import ReplayError, digest
from tests.services.relative_conformer_energy.test_service import conformer, req, run
from tests.services.scientific_read._factories import make_conformer_group, set_review
from tests.services.structure_selection._support import curated, make_world, reset_current_read_profile

R = RecordReviewStatus
BELOW = "group_below_review_floor"
TERMINAL = "group_terminal_review_status"


def reseal(manifest):
    """What a careful forger does after an edit: recompute every checksum, so only replay's own checks can catch it."""
    for section in ("calculations", "determinations"):
        for raw in manifest[section]:
            raw["content_sha256"] = digest({k: v for k, v in raw.items() if k != "content_sha256"})
    body = {k: v for k, v in manifest.items() if k != "integrity"}
    manifest["integrity"]["manifest_sha256"] = digest(body)
    return manifest


def _review(world, record_type, row, status):
    set_review(world.session, record_type=record_type, record_id=row.id, status=status)


@pytest.fixture
def held_manifest(db_session):
    """Every kind of hold-out at once: a group below the floor, a rejected group, a determination held by the floor
    and one held because it is terminal."""
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400, approve=True)
    below, below_obs = conformer(world, -76.401)
    _review(world, SubmissionRecordType.conformer_group, below, R.under_review)
    _review(world, SubmissionRecordType.conformer_observation, below_obs, R.approved)
    dead, _ = conformer(world, -76.402)
    _review(world, SubmissionRecordType.conformer_group, dead, R.rejected)
    floored, floored_obs = conformer(world, -76.403)
    _review(world, SubmissionRecordType.conformer_group, floored, R.approved)
    _review(world, SubmissionRecordType.conformer_observation, floored_obs, R.under_review)
    termed, termed_obs = conformer(world, -76.404)
    _review(world, SubmissionRecordType.conformer_group, termed, R.approved)
    _review(world, SubmissionRecordType.conformer_observation, termed_obs, R.rejected)
    manifest = run(db_session, world, req(world, ref, min_review_status=R.approved)).manifest
    return manifest, {"below": below.public_ref, "dead": dead.public_ref, "floored": floored.public_ref, "termed": termed.public_ref}


@pytest.fixture
def plain_manifest(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400)
    conformer(world, -76.405)
    conformer(world, None)
    return run(db_session, world, req(world, ref)).manifest


@pytest.fixture
def curated_manifest(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400, approve=True)
    bare = make_conformer_group(db_session, world.entry)
    _review(world, SubmissionRecordType.conformer_group, bare, R.approved)
    token = curated()
    try:
        return run(db_session, world, req(world, ref)).manifest, bare.public_ref
    finally:
        reset_current_read_profile(token)


def test_a_manifest_with_every_kind_of_hold_out_replays(held_manifest):
    manifest, refs = held_manifest
    pop = manifest["population"]
    assert pop["excluded_groups"] == {refs["below"]: BELOW, refs["dead"]: TERMINAL}
    assert pop["floor_excluded_groups"] == [refs["floored"]]
    assert pop["terminal_excluded_groups"] == [refs["termed"]]
    assert replay_matches(manifest) is True
    reasons = {m["conformer_ref"]: m["absent_reason"] for m in manifest["result"]["members"]}
    assert reasons[refs["floored"]] == "determination_below_review_floor"
    assert reasons[refs["termed"]] == "determination_terminal_review_status"
    assert reasons[refs["below"]] == BELOW and reasons[refs["dead"]] == TERMINAL


@pytest.mark.parametrize("which", ["floor", "terminal", "below", "dead"])
def test_removing_one_held_group_from_a_sealed_manifest_changes_the_answer_and_is_refused(held_manifest, which):
    manifest, refs = held_manifest
    forged = copy.deepcopy(manifest)
    pop = forged["population"]
    if which == "floor":
        pop["floor_excluded_groups"] = []
    elif which == "terminal":
        pop["terminal_excluded_groups"] = []
    else:
        del pop["excluded_groups"][refs[which]]
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "result_mismatch"


def _refused(forged, message):
    with pytest.raises(ReplayError, match=message) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "inconsistent_manifest"


def _bare(manifest):
    with_det = {d["conformer_group_ref"] for d in manifest["determinations"]}
    return next(g for g in manifest["population"]["groups"] if g not in with_det)


def test_a_curated_manifest_with_an_excluded_group_is_refused(curated_manifest):
    manifest, bare = curated_manifest
    assert replay_matches(manifest) is True
    forged = copy.deepcopy(manifest)
    forged["population"]["excluded_groups"] = {bare: TERMINAL}
    _refused(forged, "curated profile")


def test_an_excluded_group_that_was_not_compared_is_refused(plain_manifest):
    forged = copy.deepcopy(plain_manifest)
    forged["population"]["excluded_groups"] = {"cg_nowhere": TERMINAL}
    _refused(forged, "excluded group is not among")


def test_an_excluded_group_with_a_reason_that_is_not_a_group_exclusion_is_refused(plain_manifest):
    forged = copy.deepcopy(plain_manifest)
    forged["population"]["excluded_groups"] = {_bare(plain_manifest): "no_structure_determination"}
    _refused(forged, "not a group exclusion")


@pytest.mark.parametrize("field", ["floor_excluded_groups", "terminal_excluded_groups"])
def test_a_held_group_that_was_not_compared_is_refused(plain_manifest, field):
    forged = copy.deepcopy(plain_manifest)
    forged["population"][field] = ["cg_nowhere"]
    _refused(forged, "held-out group is not among")


def test_a_group_both_excluded_and_held_is_refused(plain_manifest):
    forged = copy.deepcopy(plain_manifest)
    bare = _bare(plain_manifest)
    forged["population"]["excluded_groups"] = {bare: TERMINAL}
    forged["population"]["terminal_excluded_groups"] = [bare]
    _refused(forged, "excluded and held out")


def test_a_group_held_out_for_both_floor_and_terminal_is_refused(plain_manifest):
    forged = copy.deepcopy(plain_manifest)
    bare = _bare(plain_manifest)
    forged["population"]["floor_excluded_groups"] = [bare]
    forged["population"]["terminal_excluded_groups"] = [bare]
    _refused(forged, "held out twice")


def test_a_recorded_determination_in_an_excluded_group_is_refused(plain_manifest):
    forged = copy.deepcopy(plain_manifest)
    forged["population"]["excluded_groups"] = {forged["determinations"][0]["conformer_group_ref"]: TERMINAL}
    _refused(forged, "belongs to an excluded group")


def test_a_below_floor_claim_is_refused_when_no_floor_was_set(plain_manifest):
    forged = copy.deepcopy(plain_manifest)
    forged["population"]["excluded_groups"] = {_bare(plain_manifest): BELOW}
    _refused(forged, "no review floor is set")
    again = copy.deepcopy(plain_manifest)
    again["population"]["floor_excluded_groups"] = [_bare(plain_manifest)]
    _refused(again, "no review floor is set")
