"""The relative-energy manifest and its replay."""

from __future__ import annotations

import copy
import json

import pytest

from app.services.relative_conformer_energy import replay_matches, replay_relative_energies
from app.services.structure_selection.manifest import ReplayError, canonical, digest
from tests.services.relative_conformer_energy.test_service import conformer, req, run
from tests.services.structure_selection._support import make_world


@pytest.fixture
def manifest(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400)
    conformer(world, -76.405)
    conformer(world, None)
    return run(db_session, world, req(world, ref)).manifest


def reseal(manifest):
    """What a careful forger does after an edit: recompute every checksum, so only recomputation can catch it."""
    for section in ("calculations", "determinations"):
        for raw in manifest[section]:
            raw["content_sha256"] = digest({k: v for k, v in raw.items() if k != "content_sha256"})
    body = {k: v for k, v in manifest.items() if k != "integrity"}
    manifest["integrity"]["manifest_sha256"] = digest(body)
    return manifest


def test_a_manifest_is_plain_json_and_replays_to_the_recorded_answer(manifest):
    assert json.loads(canonical(manifest)) == manifest
    assert manifest["format"] == {"name": "relative_conformer_energy_manifest", "version": 5}
    assert replay_matches(manifest) is True
    assert replay_relative_energies(manifest)["outcome"] == "relative_energies"
    assert len(manifest["determinations"]) == 2 and len(manifest["population"]["groups"]) == 3


def test_an_edited_stored_value_is_caught_by_recomputation_even_with_every_checksum_recomputed(manifest):
    forged = copy.deepcopy(manifest)
    sp = next(c for c in forged["calculations"] if c["energy"]["sp_electronic_hartree"] is not None)
    sp["energy"]["sp_electronic_hartree"] = -70.0
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "assessment_mismatch"


def test_an_edited_answer_is_caught_even_with_every_checksum_recomputed(manifest):
    forged = copy.deepcopy(manifest)
    # A non-reference member that has a value (members are in random ref order, so never pick by position).
    target = next(m for m in forged["result"]["members"] if m["status"] == "present")
    target["relative_energy_kj_mol"] = target["relative_energy_kj_mol"] + 1.0
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "result_mismatch"


def test_an_unsealed_edit_fails_its_checksum(manifest):
    forged = copy.deepcopy(manifest)
    forged["result"]["basis"] = "something else"
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(forged)
    assert raised.value.code == "manifest_checksum_mismatch"


def test_a_manifest_of_another_semantic_version_is_refused_not_substituted(manifest):
    forged = copy.deepcopy(manifest)
    forged["versions"]["relative_energy"] = "0"
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "unsupported_semantic_version"


@pytest.mark.parametrize("bad", [True, 1.0])
def test_a_bool_or_float_semantic_version_is_not_the_integer_it_compares_equal_to(manifest, bad):
    """Kills: ``versions.get(name) != value`` letting ``true`` pass for version 1."""
    forged = copy.deepcopy(manifest)
    forged["versions"]["relative_energy"] = bad
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "unsupported_semantic_version"


@pytest.mark.parametrize("version", ["1", "2", "4"])
def test_a_manifest_of_a_retired_or_unwritten_semantic_version_is_refused(manifest, version):
    """Kills: a legacy projection (versions 1 and 2) re-added to replay.

    Version 1 recorded no ``value_geometry_ref``; version 2 carried every field. Neither is read any more: only the
    running version replays, and the refusal is by version, not by shape."""
    forged = copy.deepcopy(manifest)
    forged["versions"]["relative_energy"] = version
    forged["result"]["relative_energy_version"] = version
    if version == "1":
        for member in forged["result"]["members"]:
            del member["value_geometry_ref"]
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "unsupported_semantic_version"


def test_a_current_version_manifest_missing_the_field_is_refused(manifest):
    forged = copy.deepcopy(manifest)
    for member in forged["result"]["members"]:
        del member["value_geometry_ref"]
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "result_mismatch"


def test_the_writer_stamps_the_registered_format_and_the_reader_refuses_a_float_bool_or_missing_one(manifest):
    from app.services.relative_conformer_energy.manifest import MANIFEST_FORMAT, MANIFEST_FORMATS
    from tests.services._format_cases import bad_format_manifests

    current = MANIFEST_FORMATS.current(MANIFEST_FORMAT).version
    assert manifest["format"] == {"name": MANIFEST_FORMAT, "version": current} and type(current) is int
    for tampered in bad_format_manifests(manifest, current):
        with pytest.raises(ReplayError, match="unknown manifest format"):
            replay_relative_energies(tampered)


def test_a_version_one_manifest_is_refused_as_an_unknown_format_not_as_a_decision_mismatch(manifest):
    """Kills: bumping neither format version, so a manifest whose structure request carried retired fields replays."""
    from app.services.relative_conformer_energy.manifest import MANIFEST_FORMAT

    old = copy.deepcopy(manifest)
    old["format"] = {"name": MANIFEST_FORMAT, "version": 1}
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(old))
    assert raised.value.code == "unsupported_manifest_format"
    assert "unknown manifest format" in str(raised.value)


def test_the_structure_administrative_key_version_is_in_the_recorded_versions(manifest):
    """Kills: leaving the key version out, so a changed representative choice replays under the old document."""
    from app.services.structure_selection.decision import ADMIN_KEY_VERSION

    assert manifest["versions"]["administrative_key"] == ADMIN_KEY_VERSION
    forged = copy.deepcopy(manifest)
    forged["versions"]["administrative_key"] = ADMIN_KEY_VERSION + 1
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "unsupported_semantic_version"


@pytest.fixture
def curated_manifest_over_an_unreviewed_observation(db_session):
    """A genuine curated manifest: two approved groups; one group's only observation has no review row (``not_reviewed``)."""
    from app.db.models.common import RecordReviewStatus, SubmissionRecordType
    from tests.services.scientific_read._factories import set_review
    from tests.services.structure_selection._support import curated, reset_current_read_profile

    world = make_world(db_session)
    ref, _ = conformer(world, -76.400, approve=True)
    second, _ = conformer(world, -76.405)  # the observation stays unreviewed
    set_review(db_session, record_type=SubmissionRecordType.conformer_group, record_id=second.id, status=RecordReviewStatus.approved)
    token = curated()
    try:
        return run(db_session, world, req(world, ref)).manifest
    finally:
        reset_current_read_profile(token)


def test_a_curated_manifest_over_an_unreviewed_observation_replays(curated_manifest_over_an_unreviewed_observation):
    """ADR 0023 (Q2): the observation inside an approved group is evidence, so a curated manifest records it unreviewed and the
    replay accepts it (the statuses it records are ``approved`` and ``not_reviewed``).

    Kills: the replay judging a determination by the record floor again (``recorded`` instead of the evidence rule).
    """
    manifest = curated_manifest_over_an_unreviewed_observation
    assert manifest["read_profile"]["profile"] == "curated"
    recorded = {(d["group_review_status"], d["observation_review_status"], d["review_status"]) for d in manifest["determinations"]}
    assert recorded == {("approved", "approved", "approved"), ("approved", "not_reviewed", "not_reviewed")}
    assert replay_matches(manifest) is True


def test_the_replay_judges_the_group_by_the_record_floor_and_the_observation_by_the_evidence_rule(
    curated_manifest_over_an_unreviewed_observation,
):
    """The combined status alone cannot tell a ``not_reviewed`` that came from the observation (evidence, allowed) from one
    that came from the group (a record, which a curated request cannot return). They are recorded apart.

    Kills: a replay that reads only the combined ``review_status``.
    """
    manifest = curated_manifest_over_an_unreviewed_observation
    unreviewed = next(d for d in manifest["determinations"] if d["observation_review_status"] == "not_reviewed")

    def edited(**changes):
        forged = copy.deepcopy(manifest)
        target = next(d for d in forged["determinations"] if d["determination_ref"] == unreviewed["determination_ref"])
        target.update(changes)
        return reseal(forged)

    # The group is below the floor (and the combined status says so): a curated request could not have returned it.
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(edited(group_review_status="not_reviewed"))
    assert raised.value.code == "inconsistent_manifest"
    # A rejected observation is withheld evidence: never an input.
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(edited(observation_review_status="rejected", review_status="rejected"))
    assert raised.value.code == "inconsistent_manifest"
    # The combined status must be the weaker of the two.
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(edited(review_status="approved"))
    assert raised.value.code == "inconsistent_manifest"
