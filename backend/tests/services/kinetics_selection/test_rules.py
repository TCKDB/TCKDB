"""The kinetics rule registry and the XYG3 barrier rule's predicates.

Every prerequisite is stated-and-equal (true), stated-and-different (false) or unstated (unknown), and only true
makes an edge. The rule ships inactive, so these tests drive its predicates directly (and through a copy whose
manifest is, hypothetically, approved) to show what it *would* do and that nothing unstated can earn an edge.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from app.chemistry.kinetics_rules.xyg3_barrier_manifest import MANIFEST_PATH, parse_xyg3_barrier_manifest
from app.services.kinetics_selection.models import KineticsSubject, SpeciesFact
from app.services.kinetics_selection.rules import (
    COMPATIBILITY_FIELDS,
    EDGE_LABEL,
    RULE_ACTIVE,
    RULE_INACTIVE,
    XYG3B3LYPBarrierRule,
    active_rules,
    default_rules,
)
from app.services.selection_kernel import Tri
from tests.services.kinetics_selection._support import norm, request

RAW = yaml.safe_load(MANIFEST_PATH.read_bytes())
H_KEY, H2_KEY = "YZCKVEUIGOORGS-UHFFFAOYSA-N", "UFHFLCQGNIYNRP-UHFFFAOYSA-N"
HCL_KEY, CL_KEY = "VEXZGXHMUGYJMC-UHFFFAOYSA-N", "ZAMOUSCENKQFHK-UHFFFAOYSA-N"
RADICAL_KEYS = {H_KEY: 2, CL_KEY: 2}


def fact(inchi_key, multiplicity=1, *, charge=0, entry_kind="minimum", isotope_key=None, state="ground", ref=None):
    return SpeciesFact(ref or f"spe_{inchi_key[:6]}", inchi_key, charge, multiplicity, isotope_key, state, entry_kind)


def h_plus_hcl() -> KineticsSubject:
    """H + HCl -> H2 + Cl, the first reaction of the HT set, in its listed orientation."""
    return KineticsSubject("rxe_x", (fact(H_KEY, 2), fact(HCL_KEY)), (fact(H2_KEY), fact(CL_KEY, 2)))


def hypothetically_active() -> XYG3B3LYPBarrierRule:
    raw = copy.deepcopy(RAW)
    raw["status"].update(
        activation_approved=True, activation_blockers=[], curator_acceptance={"accepted_by": "owner", "date": "2026-10-05"}
    )
    return XYG3B3LYPBarrierRule(parse_xyg3_barrier_manifest(raw))


RULE = XYG3B3LYPBarrierRule()


# -- the registry ---------------------------------------------------------------------------------------


def test_nothing_in_this_release_is_active_and_every_entry_says_why():
    rules = default_rules()
    assert [r.rule_id for r in rules] == ["K-XYG3-B3LYP-BARRIER", "K-HO2-TBA-MULTIPATH", "K-MBH-TUNNELING", "K-ME-CONVERGENCE"]
    assert all(r.status == RULE_INACTIVE for r in rules) and active_rules() == ()
    for rule in rules:
        entry = rule.describe()
        assert entry["inactive_reasons"], rule.rule_id
        assert entry["edge_label"] == EDGE_LABEL == "expected-performance inference"
        assert entry["objective_key"] and entry["version"]
    # Multipath and tunneling were never audited from primary sources, and say so.
    for rule_id in ("K-HO2-TBA-MULTIPATH", "K-MBH-TUNNELING"):
        reasons = " ".join(next(r for r in rules if r.rule_id == rule_id).inactive_reasons)
        assert "abstract-level evidence only" in reasons and "never activated from an abstract" in reasons


def test_the_inactive_council_examples_make_no_scope_and_no_side():
    inactive = [r for r in default_rules() if r.rule_id != "K-XYG3-B3LYP-BARRIER"]
    for rule in inactive:
        assert rule.scope(h_plus_hcl(), request()).state is Tri.unknown
        assert rule.preferred_side(norm()).state is Tri.unknown and rule.yielding_side(norm()).state is Tri.unknown


def test_the_active_rules_filter_follows_the_statuses_given():
    chosen = active_rules((RULE, hypothetically_active()))
    assert [r.status for r in chosen] == [RULE_ACTIVE] and chosen[0].manifest.activatable
    assert active_rules((RULE,)) == ()


def test_the_registry_entry_carries_the_manifest_digest_the_edges_label_and_the_limits():
    entry = RULE.describe()
    assert entry["manifest_sha256"] == RULE.manifest.sha256
    assert entry["manifest"]["activation_blockers"] and entry["evidence_limits"]
    assert entry["compatibility_fields"] == [*COMPATIBILITY_FIELDS, "departures"]
    assert "not a per-reaction guarantee" in entry["interpretation"]


# -- scope ----------------------------------------------------------------------------------------------


def test_a_manifest_reaction_is_in_scope_as_listed_and_reversed():
    listed = RULE.scope(h_plus_hcl(), request())
    assert listed.state is Tri.true and "orientation:as_listed" in listed.reasons
    reversed_subject = KineticsSubject("rxe_x", h_plus_hcl().products, h_plus_hcl().reactants)
    flipped = RULE.scope(reversed_subject, request())
    assert flipped.state is Tri.true and "orientation:reversed" in flipped.reasons
    assert any(r.startswith("manifest_member:HT01") for r in listed.reasons)


@pytest.mark.parametrize(
    "subject,reason",
    [
        (KineticsSubject("rxe_x", (fact(H_KEY, 2), fact(HCL_KEY)), (fact(H2_KEY), fact(CL_KEY, 2), fact(H_KEY, 2))), "reaction_not_in_manifest"),
        (KineticsSubject("rxe_x", (fact(H_KEY, 4), fact(HCL_KEY)), (fact(H2_KEY), fact(CL_KEY, 2))), "reaction_not_in_manifest"),
        (KineticsSubject("rxe_x", (fact(H_KEY, 2, charge=1), fact(HCL_KEY)), (fact(H2_KEY), fact(CL_KEY, 2))), "reaction_not_in_manifest"),
        (KineticsSubject("rxe_x", (fact(H_KEY, 2), fact(HCL_KEY, entry_kind="vdw_complex")), (fact(H2_KEY), fact(CL_KEY, 2))), "reaction_not_in_manifest"),
        (KineticsSubject("rxe_x", (fact(H_KEY, 2, isotope_key="2H"), fact(HCL_KEY)), (fact(H2_KEY), fact(CL_KEY, 2))), "isotopologue_is_not_a_manifest_member"),
        (KineticsSubject("rxe_x", (fact("A" * 14 + "-UHFFFAOYSA-N"),), (fact("B" * 14 + "-UHFFFAOYSA-N"),)), "reaction_not_in_manifest"),
    ],
    ids=["extra_product", "other_multiplicity", "other_charge", "other_entry_kind", "isotopologue", "unknown_species"],
)
def test_anything_that_is_not_exactly_a_member_is_out_of_scope(subject, reason):
    verdict = RULE.scope(subject, request())
    assert verdict.state is Tri.false and reason in verdict.reasons


# -- the sides: unstated is unknown, stated and different is false ------------------------------------------


def level(method, basis, *, source="protocol_declared", ref="calc_1"):
    return {"source": source, "role": "electronic_energy" if source == "protocol_declared" else "ts_energy",
            "calculation_ref": ref, "method": method, "basis": basis}


FULL_PROTOCOL = {
    "version": 1, "method_kind": "saddle_point_tst", "barrier_basis": "classical_electronic",
    "geometry_relation": "optimized_at_other_level", "zero_point_treatment": "harmonic_scaled",
    "rotor_treatment": "rigid_rotor_harmonic_oscillator", "conformer_treatment": "single_conformer",
    "path_treatment": "single_path", "departures": [],
}


def rec(method="XYG3", basis="6-311+G(3df,2p)", *, protocol=FULL_PROTOCOL, levels=None, **changes):
    levels = [level(method, basis)] if levels is None else levels
    fields = {
        "scientific_origin": "computed",
        "protocol_state": "valid" if protocol is not None else "absent",
        "protocol": protocol,
        "energy_levels": tuple(levels),
    }
    fields.update(changes)
    return norm(**fields)


def test_a_fully_stated_xyg3_record_is_the_preferred_side_and_not_the_yielding_one():
    xyg3 = rec("XYG3")
    assert RULE.preferred_side(xyg3).state is Tri.true and RULE.yielding_side(xyg3).state is Tri.false
    b3lyp = rec("B3LYP")
    assert RULE.yielding_side(b3lyp).state is Tri.true and RULE.preferred_side(b3lyp).state is Tri.false


def test_labels_are_compared_without_case_spaces_or_the_old_star_notation():
    for method, basis in (("xyg3", "6-311+g(3df,2p)"), (" XYG3 ", "6-311+G (3df, 2p)"), ("XYG3", "6-311+G(3df,2p)*")):
        assert RULE.preferred_side(rec(method, basis)).state is Tri.true, (method, basis)


@pytest.mark.parametrize(
    "changes,state,reason",
    [
        ({"scientific_origin": "experimental"}, Tri.false, "origin_not_computed:experimental"),
        ({"protocol": None}, Tri.unknown, "protocol_not_declared"),
        ({"protocol_state": "unreadable", "protocol": None}, Tri.unknown, "protocol_declaration_unreadable"),
        ({"levels": []}, Tri.unknown, "electronic_energy_calculation_not_declared"),
        ({"basis": "def2-TZVP"}, Tri.false, "declared_energy_level:XYG3/def2-TZVP"),
        ({"method": "B3LYP"}, Tri.false, "declared_energy_level:B3LYP/6-311+G(3df,2p)"),
        ({"levels": [level("XYG3", None)]}, Tri.unknown, "declared_energy_level_incomplete"),
        ({"levels": [level("XYG3", "6-311+G(3df,2p)"), level("XYG3", "6-31G*", ref="calc_2")]}, Tri.false, "declared_energy_level:XYG3/6-31G*"),
        ({"levels": [level("XYG3", "6-311+G(3df,2p)"), level("CCSD(T)", "cc-pVTZ", source="source_link")]}, Tri.false, "linked_energy_level_names_another_method:CCSD(T)"),
    ],
    ids=["experimental", "no_protocol", "unreadable_protocol", "no_energy_level", "other_basis", "other_method",
         "incomplete_level", "one_of_two_levels_differs", "linked_level_names_another_method"],
)
def test_the_xyg3_side_is_false_or_unknown_for_anything_not_established(changes, state, reason):
    verdict = RULE.preferred_side(rec(**changes))
    assert verdict.state is state and reason in verdict.reasons


@pytest.mark.parametrize(
    "field,state,reason",
    [
        ("method_kind", Tri.unknown, "method_kind_not_stated"),
        ("barrier_basis", Tri.unknown, "barrier_basis_not_stated"),
        ("geometry_relation", Tri.unknown, "geometry_relation_not_stated"),
    ],
)
def test_a_component_the_record_does_not_state_makes_the_side_unknown_never_true(field, state, reason):
    protocol = {k: v for k, v in FULL_PROTOCOL.items() if k != field}
    for side in (RULE.preferred_side(rec("XYG3", protocol=protocol)), RULE.yielding_side(rec("B3LYP", protocol=protocol))):
        assert side.state is state and reason in side.reasons


@pytest.mark.parametrize(
    "changes,reason",
    [({"method_kind": "variational_tst"}, "method_kind:variational_tst"),
     ({"method_kind": "experimental"}, "method_kind:experimental"),
     ({"barrier_basis": "zpe_corrected"}, "barrier_basis:zpe_corrected")],
)
def test_a_stated_different_component_is_false_for_both_sides(changes, reason):
    protocol = {**FULL_PROTOCOL, **changes}
    assert reason in RULE.preferred_side(rec("XYG3", protocol=protocol)).reasons
    assert RULE.preferred_side(rec("XYG3", protocol=protocol)).state is Tri.false
    assert RULE.yielding_side(rec("B3LYP", protocol=protocol)).state is Tri.false


def test_a_source_link_alone_never_supplies_the_method_but_a_matching_one_corroborates():
    only_link = rec("XYG3", levels=[level("XYG3", "6-311+G(3df,2p)", source="source_link")])
    assert RULE.preferred_side(only_link).state is Tri.unknown  # no declared electronic-energy calculation
    both = rec("XYG3", levels=[level("XYG3", "6-311+G(3df,2p)"), level("XYG3", "6-311+G(3df,2p)", source="source_link", ref="calc_2")])
    verdict = RULE.preferred_side(both)
    assert verdict.state is Tri.true and "energy_level_declared_and_corroborated_by_a_link" in verdict.reasons
    declared_only = RULE.preferred_side(rec("XYG3"))
    assert "energy_level_declared_only" in declared_only.reasons


# -- the pair: everything the rule does not compare must be verified equal -------------------------------


def test_a_pair_that_agrees_on_every_other_component_is_compatible():
    verdict = RULE.compatible(rec("XYG3"), rec("B3LYP"))
    assert verdict.state is Tri.true and len(verdict.reasons) == len(COMPATIBILITY_FIELDS) + 1


@pytest.mark.parametrize("name", [*COMPATIBILITY_FIELDS, "departures"])
def test_one_unstated_component_on_either_side_makes_the_pair_unknown(name):
    stripped = {k: v for k, v in FULL_PROTOCOL.items() if k != name}
    for left, right in ((rec("XYG3", protocol=stripped), rec("B3LYP")), (rec("XYG3"), rec("B3LYP", protocol=stripped))):
        verdict = RULE.compatible(left, right)
        assert verdict.state is Tri.unknown and f"{name}_not_stated_on_both_sides" in verdict.reasons


@pytest.mark.parametrize(
    "name,other",
    [("zero_point_treatment", "harmonic_unscaled"), ("rotor_treatment", "hindered_rotor"),
     ("conformer_treatment", "boltzmann_ensemble"), ("path_treatment", "multipath_full"),
     ("geometry_relation", "optimized_at_energy_level")],
)
def test_a_component_stated_differently_makes_the_pair_incompatible(name, other):
    verdict = RULE.compatible(rec("XYG3"), rec("B3LYP", protocol={**FULL_PROTOCOL, name: other}))
    assert verdict.state is Tri.false and any(r.startswith(f"{name}_differs") for r in verdict.reasons)


def test_departures_are_compared_as_sets_and_a_tunneling_departure_on_one_side_blocks_the_pair():
    same = RULE.compatible(rec("XYG3", protocol={**FULL_PROTOCOL, "departures": ["tunneling", "geometry"]}),
                           rec("B3LYP", protocol={**FULL_PROTOCOL, "departures": ["geometry", "tunneling"]}))
    assert same.state is Tri.true
    differ = RULE.compatible(rec("XYG3", protocol={**FULL_PROTOCOL, "departures": ["tunneling"]}), rec("B3LYP"))
    assert differ.state is Tri.false and "departures_differ" in differ.reasons


def test_a_pair_with_an_undeclared_protocol_is_unknown():
    assert RULE.compatible(rec("XYG3", protocol=None), rec("B3LYP")).state is Tri.unknown
    assert RULE.compatible(rec("XYG3"), rec("B3LYP", protocol=None)).state is Tri.unknown


def test_a_refuted_prerequisite_outranks_an_unknown_one_on_the_same_side():
    # A zero-point-corrected barrier is refuted even though the geometry relation is unstated alongside it.
    protocol = {k: v for k, v in FULL_PROTOCOL.items() if k != "geometry_relation"}
    protocol["barrier_basis"] = "zpe_corrected"
    side = RULE.preferred_side(rec("XYG3", protocol=protocol))
    assert side.state is Tri.false
    assert "barrier_basis:zpe_corrected" in side.reasons and "geometry_relation_not_stated" in side.reasons


def test_the_rule_refuses_to_load_over_a_manifest_that_is_not_the_pinned_one(monkeypatch):
    from app.chemistry.kinetics_rules.xyg3_barrier_manifest import ManifestError
    from app.services.kinetics_selection import rules

    monkeypatch.setattr(rules, "XYG3_MANIFEST_SHA256", "0" * 64)
    with pytest.raises(ManifestError, match="pinned digest"):
        XYG3B3LYPBarrierRule()
