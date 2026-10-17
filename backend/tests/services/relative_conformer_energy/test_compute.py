"""The pure relative-energy computation. No database: normalised basins in, public refs out.

Each test below is paired, in the PR description, with the wrong change it exists to catch.
"""

from __future__ import annotations

import json
from dataclasses import replace

from app.chemistry.units import HARTREE_TO_KJ_MOL
from app.services.relative_conformer_energy.models import (
    BASIS_SURFACE_NOTE,
    Certificate,
    EnergyKind,
    LevelSelector,
    MemberStatus,
    RelativeEnergyOutcome,
)
from app.services.structure_selection.models import RepeatPolicy
from tests.services.relative_conformer_energy._support import (
    LOT_A,
    LOT_B,
    STIFF_IMAGINARY,
    Basin,
    basin,
    compute,
    member,
    request,
)
from tests.services.structure_selection._support import declaration

Out = RelativeEnergyOutcome
M = MemberStatus
ALL_ELECTRON = declaration(core_treatment={"state": "known", "value": "all_electron"})


# -- missing is missing ------------------------------------------------------------------------------------------


def test_a_group_with_no_determination_is_absent_and_never_zero():
    r = compute(request(), ["cg_a", "cg_b"], basin("cg_a"))
    b = member(r, "cg_b")
    assert b.status is M.absent and b.absent_reason == "no_structure_determination"
    assert (b.value_hartree, b.relative_energy_hartree, b.relative_energy_kj_mol) == (None, None, None)
    assert r.outcome is Out.reference_only
    assert r.coverage["present"] == 1 and r.coverage["absent"] == 1 and r.coverage["complete"] is False


def test_a_value_at_another_level_is_not_used_for_the_requested_one():
    r = compute(request(), ["cg_a", "cg_b"], basin("cg_a", energy=-76.40), basin("cg_b", energy=-76.50, lot=LOT_B))
    b = member(r, "cg_b")
    assert b.status is M.absent and b.absent_reason == "no_value_at_requested_level"
    assert b.value_hartree is None and r.outcome is Out.reference_only


def test_a_level_bound_to_a_composite_scheme_is_named_in_the_absence():
    bound = basin("cg_b", energy=-76.5)
    # The level facts are frozen; rebuild the energy calculation bound to a scheme.
    calcs = tuple(
        replace(c, level=replace(c.level, composite_scheme_ref="csch_x")) if c.type == "sp" else c for c in bound.calcs
    )
    bound = Basin(bound.det, calcs)
    r = compute(request(), ["cg_a", "cg_b"], basin("cg_a"), bound)
    b = member(r, "cg_b")
    assert b.absent_reason == "no_value_at_requested_level" and "level_bound_to_composite_scheme" in b.detail
    named = compute(
        request(level=LevelSelector(composite_scheme_ref="csch_x"), reference_conformer_ref="cg_b"), ["cg_a", "cg_b"], bound
    )
    assert member(named, "cg_b").status is M.reference


# -- one level, one cohort --------------------------------------------------------------------------------------


def test_two_recipe_cohorts_at_the_level_compute_nothing_and_list_both():
    r = compute(
        request(), ["cg_a", "cg_b"], basin("cg_a", energy=-76.40), basin("cg_b", energy=-76.41, decl=ALL_ELECTRON)
    )
    assert r.outcome is Out.multiple_recipes_at_level
    assert all(m.relative_energy_hartree is None and m.relative_energy_kj_mol is None for m in r.members)
    assert all(m.status is M.unresolved for m in r.members)
    assert len(r.cohorts) == 2 and all(c["recipe_facts"] for c in r.cohorts)
    assert r.recipe_cohort_id is None and r.unresolved[0]["code"] == "multiple_recipes_at_level"


def test_a_recipe_narrows_within_the_level():
    narrowed = request(recipe=declaration(core_treatment={"state": "known", "value": "frozen_core"}))
    r = compute(narrowed, ["cg_a", "cg_b"], basin("cg_a", energy=-76.40), basin("cg_b", energy=-76.41, decl=ALL_ELECTRON))
    assert member(r, "cg_b").absent_reason == "recipe_differs_from_request"
    assert r.outcome is Out.reference_only and r.recipe_cohort_id is not None


# -- E_elec and E0 are different quantities ----------------------------------------------------------------------


def test_an_sp_energy_is_never_a_zero_kelvin_energy():
    req = request(energy_kind=EnergyKind.zero_kelvin_energy)
    r = compute(req, ["cg_a", "cg_b"], basin("cg_a", kind="e0", energy=-76.40), basin("cg_b", kind="electronic", quantity="zero_kelvin_energy"))
    b = member(r, "cg_b")
    assert b.status is M.absent and b.absent_reason == "no_determination_supplies_requested_kind"
    assert b.value_hartree is None and r.outcome is Out.reference_only
    assert member(r, "cg_a").status is M.reference


def test_a_composite_e0_is_a_value_of_the_zero_kelvin_energy():
    req = request(energy_kind=EnergyKind.zero_kelvin_energy)
    r = compute(req, ["cg_a", "cg_b"], basin("cg_a", kind="e0", energy=-76.40), basin("cg_b", kind="e0", energy=-76.41))
    assert r.outcome is Out.relative_energies
    assert member(r, "cg_b").relative_energy_hartree == member(r, "cg_b").value_hartree - member(r, "cg_a").value_hartree


# -- repeats ----------------------------------------------------------------------------------------------------


def test_disagreeing_repeats_are_reported_never_the_lower_one():
    r = compute(
        request(),
        ["cg_a", "cg_b"],
        basin("cg_a", energy=-76.40),
        basin("cg_b", energy=-76.40, observation="co_b1"),
        basin("cg_b", energy=-76.41, observation="co_b2"),
    )
    b = member(r, "cg_b")
    assert b.status is M.unresolved and b.absent_reason == "repeat_values_disagree"
    assert b.value_hartree is None and b.relative_energy_kj_mol is None
    assert sorted(a.value_hartree for a in b.alternates) == [-76.41, -76.40]
    assert len(b.determination_refs) == 2
    assert r.unresolved == ({"code": "repeat_values_disagree", "refs": ["cg_b"]},)


def test_agreeing_repeats_are_one_value_with_every_determination_listed():
    r = compute(
        request(),
        ["cg_a", "cg_b"],
        basin("cg_a", energy=-76.40),
        basin("cg_b", energy=-76.41, observation="co_b1"),
        basin("cg_b", energy=-76.41, observation="co_b2"),
    )
    b = member(r, "cg_b")
    assert b.status is M.present and b.value_hartree == -76.41 and b.representative is False
    assert len(b.determination_refs) == 2 and b.alternates == ()


def test_the_administrative_representative_is_the_first_in_order_not_the_lowest():
    # The newer determination is first in the administrative order (creation time, descending); it is the higher value.
    older_lower = basin("cg_b", energy=-76.41, age_days=10, observation="co_b1")
    newer_higher = basin("cg_b", energy=-76.40, age_days=0, observation="co_b2")
    r = compute(
        request(repeat_policy=RepeatPolicy.administrative_representative),
        ["cg_a", "cg_b"],
        basin("cg_a", energy=-76.30),
        older_lower,
        newer_higher,
    )
    b = member(r, "cg_b")
    assert b.status is M.present and b.representative is True
    assert b.value_hartree == -76.40 and b.determination_refs[0] != ""
    assert [a.value_hartree for a in b.alternates] == [-76.41]


# -- the reference is named, never chosen -----------------------------------------------------------------------


def test_a_reference_without_a_value_gives_no_difference_to_anyone():
    r = compute(request(), ["cg_a", "cg_b", "cg_c"], basin("cg_b", energy=-76.50), basin("cg_c", energy=-76.45))
    assert r.outcome is Out.reference_unavailable
    assert member(r, "cg_a").status is M.absent
    assert all(m.relative_energy_hartree is None and m.relative_energy_kj_mol is None for m in r.members)
    assert member(r, "cg_b").value_hartree == -76.50  # the values themselves are still reported
    assert "reference_unavailable" in member(r, "cg_b").detail


def test_a_reference_that_is_not_the_lowest_leaves_a_negative_difference_negative():
    half_kcal_below = -76.40 - 0.49 / 627.5094740631
    r = compute(
        request(),
        ["cg_a", "cg_b", "cg_c"],
        basin("cg_a", energy=-76.40),
        basin("cg_b", energy=half_kcal_below),
        basin("cg_c", energy=-76.39),
    )
    b, c = member(r, "cg_b"), member(r, "cg_c")
    assert b.relative_energy_kj_mol is not None and b.relative_energy_kj_mol < 0
    assert abs(b.relative_energy_kj_mol - (-0.49 * 4.184)) < 1e-6
    assert c.relative_energy_kj_mol is not None and c.relative_energy_kj_mol > 0
    assert member(r, "cg_a").relative_energy_kj_mol == 0.0 and member(r, "cg_a").status is M.reference


def test_the_difference_is_taken_in_hartree_and_converted_once():
    va, vb = -472.123456789, -472.123110112
    r = compute(request(), ["cg_a", "cg_b"], basin("cg_a", energy=va), basin("cg_b", energy=vb))
    b = member(r, "cg_b")
    assert b.relative_energy_hartree == vb - va
    assert b.relative_energy_kj_mol == (vb - va) * HARTREE_TO_KJ_MOL
    assert b.relative_energy_kj_mol == (vb - va) * 2625.4996394798254
    assert b.relative_energy_kj_mol != (vb * HARTREE_TO_KJ_MOL - va * HARTREE_TO_KJ_MOL)


# -- the certificate, and no inheritance ------------------------------------------------------------------------


def test_a_minimum_certified_on_another_surface_is_labelled_not_inherited():
    # The single point is at lot_b; the frequency that certifies the minimum is at lot_a.
    pair = (basin("cg_a", energy=-76.40, lot=LOT_B, freq_lot=LOT_A), basin("cg_b", energy=-76.41, lot=LOT_B, freq_lot=LOT_A))
    level_b = LevelSelector(level_of_theory_ref=LOT_B)
    any_surface = compute(request(level=level_b), ["cg_a", "cg_b"], *pair)
    b = member(any_surface, "cg_b")
    assert b.status is M.present and b.certificate is not None
    assert b.certificate.supported and b.certificate.on_energy_surface is False
    assert b.certificate.surface_level_refs == (LOT_A,)
    assert BASIS_SURFACE_NOTE in any_surface.basis

    strict = compute(request(level=level_b, certificate=Certificate.local_minimum_on_energy_surface), ["cg_a", "cg_b"], *pair)
    assert member(strict, "cg_b").absent_reason == "certificate_not_on_energy_surface"
    assert strict.outcome is Out.reference_unavailable


def test_a_minimum_certified_on_the_energy_surface_says_so():
    same = (basin("cg_a", energy=-76.40), basin("cg_b", energy=-76.41))
    r = compute(request(certificate=Certificate.local_minimum_on_energy_surface), ["cg_a", "cg_b"], *same)
    b = member(r, "cg_b")
    assert b.status is M.present and b.certificate is not None and b.certificate.on_energy_surface is True
    assert BASIS_SURFACE_NOTE not in r.basis


def test_a_basin_with_a_passing_and_a_refuting_determination_is_contested_not_passed():
    r = compute(
        request(),
        ["cg_a", "cg_b"],
        basin("cg_a", energy=-76.40),
        basin("cg_b", energy=-76.41, observation="co_b"),
        basin("cg_b", energy=-76.41, observation="co_b", curvature=STIFF_IMAGINARY),
    )
    b = member(r, "cg_b")
    assert b.status is M.absent and b.absent_reason == "contested_certificate"
    assert b.value_hartree is None and r.outcome is Out.reference_only


# -- stored uncertainties are echoed, never combined ------------------------------------------------------------


def test_stored_uncertainties_are_echoed_and_never_combined():
    r = compute(
        request(),
        ["cg_a", "cg_b"],
        basin("cg_a", energy=-76.40, uncertainty=1e-5),
        basin("cg_b", energy=-76.41, uncertainty=2e-5),
    )
    assert member(r, "cg_a").value_uncertainty_hartree == 1e-5
    assert member(r, "cg_b").value_uncertainty_hartree == 2e-5

    def keys(node, found):
        if isinstance(node, dict):
            for k, v in node.items():
                if "uncertain" in k:
                    found.add(k)
                keys(v, found)
        elif isinstance(node, list):
            for v in node:
                keys(v, found)
        return found

    assert keys(r.to_dict(), set()) == {"value_uncertainty_hartree"}
    assert json.dumps(r.to_dict())  # and the whole answer is plain JSON


# -- order is display, never a rank -----------------------------------------------------------------------------


def test_members_are_ordered_by_ref_even_when_the_energies_run_the_other_way():
    r = compute(
        request(),
        ["cg_c", "cg_a", "cg_b"],  # given out of order
        basin("cg_a", energy=-76.10),
        basin("cg_b", energy=-76.20),
        basin("cg_c", energy=-76.30),
    )
    assert [m.conformer_ref for m in r.members] == ["cg_a", "cg_b", "cg_c"]
    assert [m.value_hartree for m in r.members] == [-76.10, -76.20, -76.30]  # descending in energy order, not sorted by it
    assert all(not any("rank" in key for key in m) for m in r.to_dict()["members"])
    assert any("not a ranking" in n for n in r.notes)


# -- coverage ----------------------------------------------------------------------------------------------------


def test_a_complete_answer_asked_of_an_incomplete_population_says_so():
    from app.services.structure_selection.models import CoverageRequirement

    r = compute(request(coverage=CoverageRequirement.all_requested_members), ["cg_a", "cg_b"], basin("cg_a"))
    assert r.outcome is Out.incomplete_coverage and r.coverage["complete"] is False
    full = compute(
        request(coverage=CoverageRequirement.all_requested_members), ["cg_a", "cg_b"], basin("cg_a"), basin("cg_b", energy=-76.5)
    )
    assert full.outcome is Out.relative_energies and full.coverage["complete"] is True


def test_a_group_below_the_callers_floor_is_a_member_reported_absent():
    r = compute(request(), ["cg_a", "cg_b"], basin("cg_a"), basin("cg_b", energy=-76.5), below=frozenset({"cg_b"}))
    b = member(r, "cg_b")
    assert b.absent_reason == "group_below_review_floor" and b.value_hartree is None
