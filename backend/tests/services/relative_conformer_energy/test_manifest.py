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
    assert manifest["format"] == {"name": "relative_conformer_energy_manifest", "version": 1}
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
