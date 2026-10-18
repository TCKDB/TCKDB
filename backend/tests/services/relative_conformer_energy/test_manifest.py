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
    assert manifest["manifest_format"] == "relative_conformer_energy_manifest"
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


def as_recorded_by_version_1(manifest):
    """A manifest shaped like one the first release of the route downloaded: no ``value_geometry_ref``, version 1."""
    old = copy.deepcopy(manifest)
    for member in old["result"]["members"]:
        del member["value_geometry_ref"]
    old["result"]["relative_energy_version"] = "1"
    old["versions"]["relative_energy"] = "1"
    return reseal(old)


def test_a_manifest_recorded_by_version_1_still_replays(manifest):
    assert any(m["value_geometry_ref"] is not None for m in manifest["result"]["members"])  # the field is real
    old = as_recorded_by_version_1(manifest)
    assert all("value_geometry_ref" not in m for m in old["result"]["members"])
    assert replay_matches(old)
    assert replay_relative_energies(old) == old["result"]


def test_a_current_version_manifest_missing_the_field_is_refused_not_projected(manifest):
    forged = copy.deepcopy(manifest)
    for member in forged["result"]["members"]:
        del member["value_geometry_ref"]
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(forged))
    assert raised.value.code == "result_mismatch"


def test_the_version_1_projection_does_not_excuse_any_other_difference(manifest):
    old = as_recorded_by_version_1(manifest)
    target = next(m for m in old["result"]["members"] if m["status"] == "present")
    target["relative_energy_kj_mol"] += 1.0
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(old))
    assert raised.value.code == "result_mismatch"
    wrong_marker = as_recorded_by_version_1(manifest)
    wrong_marker["result"]["relative_energy_version"] = "2"  # the answer claims a version its manifest does not
    with pytest.raises(ReplayError) as raised:
        replay_relative_energies(reseal(wrong_marker))
    assert raised.value.code == "result_mismatch"
