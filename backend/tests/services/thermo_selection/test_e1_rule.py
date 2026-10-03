"""Rule E1 on normalised inputs: scope by species/state, and the exact standard-recipe sides."""

from __future__ import annotations

import pytest

from app.services.thermo_selection.models import Tri
from app.services.thermo_selection.rules import E1Rule
from tests.services.thermo_selection._support import BY_NAME, cand, protocol, subject_for

RULE = E1Rule()


def _state(match) -> Tri:
    return match.state


# -- scope ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [m["common_name"] for m in BY_NAME.values()])
def test_every_manifest_member_is_in_scope_with_its_own_identity(name):
    assert RULE.scope(subject_for(name)).state is Tri.true


def test_a_species_outside_the_manifest_is_out_of_scope():
    nonane = subject_for("Methane", inchi_key="BKIMMITUMNQMOS-UHFFFAOYSA-N", molecular_formula="C9H20")
    match = RULE.scope(nonane)
    assert match.state is Tri.false
    assert match.reasons == ("species_not_in_manifest",)


def test_triplet_methylene_is_out_of_scope_and_singlet_is_in():
    # Singlet and triplet CH2 share SMILES, InChI and InChIKey: only the multiplicity tells them apart.
    singlet = RULE.scope(subject_for("Methylene"))
    triplet = RULE.scope(subject_for("Methylene", multiplicity=3))
    assert singlet.state is Tri.true
    assert triplet.state is Tri.false
    assert "multiplicity_differs_from_member" in triplet.reasons[0]


def test_cyclooctatetraene_matches_on_the_first_block_and_formula_whatever_the_stereo_layer():
    for stereo_layer in ("BONZMOEMSA-N", "DLMDZQPMSA-N", "UHFFFAOYSA-N"):
        subject = subject_for("1,3,5,7-Cyclooctatetraene", inchi_key=f"KDUIUFJBNGTBMD-{stereo_layer}")
        assert RULE.scope(subject).state is Tri.true, stereo_layer


def test_cyclooctatetraene_with_the_wrong_formula_is_out_of_scope():
    subject = subject_for("1,3,5,7-Cyclooctatetraene", molecular_formula="C8H10")
    assert RULE.scope(subject).state is Tri.false


def test_a_full_key_member_does_not_match_on_its_connectivity_block_alone():
    methane = subject_for("Methane", inchi_key="VNWKTOKETHGBQD-ZZZZZZZZZZ-N")
    assert RULE.scope(methane).state is Tri.false


@pytest.mark.parametrize(
    "override",
    [{"isotope_key": "[2H]C"}, {"entry_kind": "vdw_complex"}, {"electronic_state_kind": "excited"}, {"charge": 1}],
)
def test_isotopologues_complexes_excited_states_and_ions_are_out_of_scope(override):
    assert RULE.scope(subject_for("Methane", **override)).state is Tri.false


# -- sides ------------------------------------------------------------------------------------


def test_a_standard_g4_declaration_is_the_preferred_side_only():
    c = cand("g4", proto=protocol("g4"))
    assert RULE.preferred_side(c).state is Tri.true
    assert RULE.yielding_side(c).state is Tri.false


def test_a_standard_g3_declaration_is_the_yielding_side_only():
    c = cand("g3", proto=protocol("g3"))
    assert RULE.yielding_side(c).state is Tri.true
    assert RULE.preferred_side(c).state is Tri.false


@pytest.mark.parametrize("recipe", ["g4mp2", "g4_complete"])
def test_g4_mp2_and_g4_complete_are_not_standard_g4(recipe):
    c = cand("x", proto=protocol(recipe))
    assert RULE.preferred_side(c).state is Tri.false
    assert RULE.yielding_side(c).state is Tri.false


@pytest.mark.parametrize("name", ["G3(MP2)", "G3B3", "G3X", "G4(MP2)-6X", "G3"])
def test_a_recipe_declared_as_other_never_matches_even_when_named_like_a_standard_one(name):
    c = cand("x", proto=protocol("other", other_name=name))
    assert RULE.preferred_side(c).state is Tri.false
    assert RULE.yielding_side(c).state is Tri.false


def test_any_stated_departure_prevents_a_match_on_both_sides():
    departure = [{"component": "empirical_correction", "description": "added a bond-additivity correction"}]
    for recipe, side in (("g4", RULE.preferred_side), ("g3", RULE.yielding_side)):
        match = side(cand("x", proto=protocol(recipe, departures=departure)))
        assert match.state is Tri.false
        assert "departures_declared:empirical_correction" in match.reasons


def test_departures_not_stated_is_unknown_not_standard():
    match = RULE.preferred_side(cand("x", proto=protocol("g4", departures=None)))
    assert match.state is Tri.unknown
    assert "departures_not_stated" in match.reasons


def test_no_protocol_is_unknown_and_an_unreadable_one_is_unknown():
    assert RULE.preferred_side(cand("x", proto=None)).state is Tri.unknown
    unreadable = cand("x", proto={"version": 99})
    object.__setattr__(unreadable, "protocol_state", "unreadable")
    assert RULE.preferred_side(unreadable).state is Tri.unknown


def test_an_undeclared_recipe_is_unknown():
    match = RULE.preferred_side(cand("x", proto=protocol(None)))
    assert match.state is Tri.unknown
    assert "recipe_not_declared" in match.reasons


def test_the_formation_derivation_must_be_atomization():
    isodesmic = RULE.preferred_side(cand("x", proto=protocol("g4", derivation="isodesmic")))
    undeclared = RULE.preferred_side(cand("x", proto=protocol("g4", derivation=None)))
    assert isodesmic.state is Tri.false
    assert undeclared.state is Tri.unknown


@pytest.mark.parametrize("motion", ["hindered_rotors", "anharmonic"])
def test_a_thermal_treatment_beyond_the_benchmarked_harmonic_one_prevents_a_match(motion):
    assert RULE.preferred_side(cand("x", proto=protocol("g4", internal_motion=motion))).state is Tri.false


def test_a_declared_harmonic_treatment_still_matches():
    assert RULE.preferred_side(cand("x", proto=protocol("g4", internal_motion="harmonic"))).state is Tri.true


def test_a_linked_level_that_names_another_recipe_contradicts_the_declaration():
    match = RULE.preferred_side(cand("x", proto=protocol("g4"), linked=("g4mp2",)))
    assert match.state is Tri.false
    assert "linked_level_names_other_recipe:g4mp2" in match.reasons


def test_a_linked_level_that_agrees_is_disclosed_as_corroboration_and_none_as_declaration_only():
    corroborated = RULE.preferred_side(cand("x", proto=protocol("g4"), linked=("g4",)))
    declared_only = RULE.preferred_side(cand("x", proto=protocol("g4")))
    assert corroborated.state is Tri.true and "recipe_corroborated_by_linked_level" in corroborated.reasons
    assert declared_only.state is Tri.true and "recipe_declaration_only" in declared_only.reasons


@pytest.mark.parametrize("origin", ["experimental", "estimated"])
def test_only_computed_records_match(origin):
    c = cand("x", proto=protocol("g4"), origin=origin)
    assert RULE.preferred_side(c).state is Tri.false


def test_a_refuted_prerequisite_wins_over_an_unknown_one():
    match = RULE.preferred_side(cand("x", proto=protocol("g4mp2", departures=None)))
    assert match.state is Tri.false


def test_the_registry_entry_records_version_evidence_limits_and_exclusions():
    entry = RULE.describe()
    assert entry["rule_id"] == "E1" and entry["version"] == "1.0.0"
    assert entry["manifest"]["manifest_version"] == "1.0.0" and entry["manifest"]["member_count"] == 38
    text = " ".join(str(e) for e in entry["evidence_limits"])
    assert "in-sample" in text and "0.69" in text and "0.48" in text
    assert any("radicals" in e for e in entry["exclusions"])
    assert entry["exceptions"] == []
    assert "not a per-molecule guarantee" in entry["interpretation"]
