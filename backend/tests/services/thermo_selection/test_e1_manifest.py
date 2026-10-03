"""The shipped E1 manifest loads only as what the rule needs, and refuses anything less."""

from __future__ import annotations

import copy

import pytest
import yaml

from app.chemistry.thermo_rules import e1_manifest
from app.chemistry.thermo_rules.e1_manifest import (
    MATCH_CONNECTIVITY_AND_FORMULA,
    MATCH_FULL_INCHIKEY,
    ManifestError,
    load_e1_manifest,
    parse_e1_manifest,
)


def _raw() -> dict:
    return copy.deepcopy(yaml.safe_load(e1_manifest.MANIFEST_PATH.read_text(encoding="utf-8")))


def test_the_shipped_manifest_is_the_approved_38_member_version():
    manifest = load_e1_manifest()
    assert manifest.version == "1.0.0"
    assert len(manifest.members) == 38
    assert manifest.curator_acceptance["accepted_by"].startswith("repository owner")
    assert {s["id"] for s in manifest.sources} >= {"G4_2007", "G3_1998", "G297_1997", "G399_2000"}


def test_only_cyclooctatetraene_matches_on_connectivity_and_only_ch2_is_state_specific():
    manifest = load_e1_manifest()
    by_mode = {m.member_id: m.match_mode for m in manifest.members}
    assert [m.name for m in manifest.members if m.match_mode == MATCH_CONNECTIVITY_AND_FORMULA] == [
        "C8H8 (cyclooctatetraene)"
    ]
    assert sum(1 for mode in by_mode.values() if mode == MATCH_FULL_INCHIKEY) == 37
    assert [m.name for m in manifest.members if m.state_specific] == ["CH2(1A1)"]


def test_variants_the_manifest_distinguishes_from_the_standard_recipes_are_named():
    assert set(load_e1_manifest().distinct_variants) >= {"G4_complete", "G4_MP2", "G3_MP2", "G3X", "G3B3"}


def test_a_manifest_not_approved_for_activation_does_not_load():
    raw = _raw()
    raw["status"]["activation_approved"] = False
    with pytest.raises(ManifestError, match="not approved"):
        parse_e1_manifest(raw)


def test_a_manifest_without_the_curator_acceptance_does_not_load():
    raw = _raw()
    del raw["status"]["curator_acceptance"]
    with pytest.raises(ManifestError, match="curator acceptance"):
        parse_e1_manifest(raw)


def test_a_member_count_that_disagrees_with_the_sources_does_not_load():
    raw = _raw()
    raw["members"].pop()
    with pytest.raises(ManifestError, match="lists 37 members"):
        parse_e1_manifest(raw)


def test_a_species_state_listed_twice_does_not_load():
    raw = _raw()
    raw["members"][1] = {**raw["members"][1], "inchikey": raw["members"][0]["inchikey"],
                         "multiplicity": raw["members"][0]["multiplicity"]}
    with pytest.raises(ManifestError, match="twice"):
        parse_e1_manifest(raw)


def test_an_unresolved_identity_does_not_load():
    raw = _raw()
    raw["members"][3]["identity_status"] = "unresolved"
    with pytest.raises(ManifestError, match="not resolved"):
        parse_e1_manifest(raw)
