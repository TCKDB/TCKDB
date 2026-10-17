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
    forged["result"]["members"][1]["relative_energy_kj_mol"] = 0.0
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
