"""Calculation-grain assessment: can one stored calculation supply the requested energy, and what is it a value of?"""

from __future__ import annotations

import math

import pytest

from app.services.selection_kernel import Applicability as A
from app.services.structure_selection.assessment import assess_calculation
from app.services.structure_selection.models import (
    CurvatureFacts,
    EnergyFacts,
    EnergyScope,
    Quantity,
)
from tests.services.structure_selection._support import (
    G1,
    G2,
    calc,
    codes,
    declaration,
    finding_facts,
    level,
    request,
    subject,
)


def assess(unit, **req):
    return assess_calculation(unit, request=request(**req), subject=subject())


# -- the value ----------------------------------------------------------------------------------------------


def test_a_single_point_supplies_its_recorded_value_at_its_one_input_geometry():
    a = assess(calc(type="sp", energy=-76.4))
    assert a.applicability is A.applicable and a.physically_eligible
    assert a.energy is not None
    assert (a.energy.hartree, a.energy.scope, a.energy.geometry_ref) == (-76.4, EnergyScope.recorded_single_point, G1)


def test_an_optimisation_supplies_its_own_endpoint_without_a_fabricated_single_point():
    a = assess(calc(type="opt", energy=-76.3))
    assert a.energy is not None and a.energy.scope is EnergyScope.optimized_endpoint
    assert a.energy.calculation_ref == "calc_a"


def test_an_unconverged_optimisation_keeps_a_labelled_intermediate_value_and_a_converged_unknown_is_disclosed():
    unconverged = calc(type="opt", energy=EnergyFacts(opt_final_hartree=-76.9, opt_converged=False))
    a = assess(unconverged)
    assert a.applicability is A.applicable and a.energy.scope is EnergyScope.unconverged_endpoint
    assert "optimization_not_converged_value_is_an_intermediate_geometry" in a.advisory
    unknown = calc(type="opt", energy=EnergyFacts(opt_final_hartree=-76.3, opt_converged=None))
    b = assess(unknown)
    assert "optimization_convergence_unknown" in b.advisory and b.energy.scope is EnergyScope.optimized_endpoint


def test_a_missing_energy_is_unresolved_and_never_zero():
    a = assess(calc(type="sp", energy=None))
    assert a.applicability is A.unresolved and codes(a) == ["energy_not_deposited"]
    assert a.energy is None and not a.physically_eligible


def test_a_non_finite_energy_is_incompatible_and_takes_no_rank():
    a = assess(calc(type="sp", energy=math.nan))
    assert a.applicability is A.incompatible and "energy_not_finite" in codes(a) and a.energy is None


def test_an_e0_comes_from_a_composite_and_an_electronic_energy_is_never_relabelled_as_one():
    sp = assess(calc(type="sp"), quantity=Quantity.zero_kelvin_energy)
    assert sp.applicability is A.incompatible and codes(sp) == ["record_does_not_supply_requested_quantity"]
    opt = assess(calc(type="opt"), quantity=Quantity.zero_kelvin_energy)
    assert opt.applicability is A.incompatible
    comp = assess(calc(type="composite", energy=-76.4), quantity=Quantity.zero_kelvin_energy)
    assert comp.applicability is A.applicable
    assert comp.energy is not None and comp.energy.scope is EnergyScope.composite_zero_kelvin
    assert comp.energy.hartree == pytest.approx(-76.38)
    electronic = assess(calc(type="composite", energy=-76.4))
    assert electronic.energy.scope is EnergyScope.composite_electronic and electronic.energy.hartree == -76.4


def test_an_e0_whose_zero_point_inclusion_is_not_stated_is_unresolved_never_assumed():
    e = EnergyFacts(composite_assembly="program_run", composite_e0_hartree=-76.38, composite_recipe_zpe_hartree=None)
    a = assess(calc(type="composite", energy=e), quantity=Quantity.zero_kelvin_energy)
    assert a.applicability is A.unresolved and codes(a) == ["e0_convention_not_stated"]


def test_a_composite_with_no_e0_is_unavailable_for_an_e0_request_but_still_answers_the_electronic_one():
    e = EnergyFacts(composite_assembly="program_run", composite_electronic_hartree=-76.4)
    unit = calc(type="composite", energy=e)
    assert assess(unit, quantity=Quantity.zero_kelvin_energy).applicability is A.unresolved
    assert assess(unit).applicability is A.applicable


def test_a_type_that_carries_no_energy_cannot_answer():
    a = assess(calc(type="freq", energy=None))
    assert a.applicability is A.incompatible


def test_a_stated_uncertainty_is_carried_and_an_absent_one_is_a_comparative_unknown_not_an_exclusion():
    stated = assess(calc(type="sp", energy=EnergyFacts(sp_electronic_hartree=-76.4, sp_uncertainty_hartree=1e-4)))
    assert stated.energy.uncertainty_hartree == 1e-4 and "energy_uncertainty_not_stated" not in stated.comparative_unknown
    bare = assess(calc(type="sp"))
    assert "energy_uncertainty_not_stated" in bare.comparative_unknown and bare.applicability is A.applicable


# -- the geometry target -----------------------------------------------------------------------------------


def test_a_fixed_geometry_request_admits_only_values_at_that_geometry():
    assert assess(calc(type="sp"), geometry_ref=G1).applicability is A.applicable
    other = assess(calc(type="sp"), geometry_ref=G2)
    assert other.applicability is A.incompatible and codes(other) == ["evaluated_at_other_geometry"]
    unknown = assess(calc(type="composite", output_geometry_refs=(G1, G2)), geometry_ref=G1)
    assert unknown.applicability is A.unresolved and "evaluated_geometry_unknown" in codes(unknown)


# -- the recipe --------------------------------------------------------------------------------------------


def test_an_undeclared_recipe_is_eligible_alone_but_has_no_cohort():
    a = assess(calc(declared=None))
    assert a.applicability is A.applicable and a.recipe.cohort_key is None
    assert "cohort_not_established" in a.comparative_unknown
    assert any(x.startswith("recipe_fact_unestablished:") for x in a.advisory)


def test_a_declaration_that_contradicts_the_level_makes_the_unit_unresolved():
    a = assess(calc(level=level(spin_treatment="unrestricted"), declared=declaration(spin_treatment={"state": "known", "value": "restricted"})))
    assert a.applicability is A.unresolved and "recipe_conflict:spin_treatment" in codes(a) and a.energy is None


def test_an_unreadable_declaration_is_disclosed_and_reads_as_not_declared():
    a = assess(calc(declared=None, declaration_state="unreadable"))
    assert "actual_protocol_declaration_unreadable" in a.advisory and a.recipe.cohort_key is None


def test_a_declared_root_that_is_not_the_ground_state_contradicts_a_ground_state_entry():
    a = assess(calc(declared=declaration(electronic_state={"state": "known", "root": 2})))
    assert a.applicability is A.incompatible and "declared_root_is_not_the_ground_state_of_the_entry" in codes(a)
    assert assess(calc(declared=declaration(electronic_state={"state": "known", "root": 0}))).applicability is A.applicable


def test_a_requested_recipe_the_unit_meets_differs_from_or_does_not_establish():
    met = assess(calc(), recipe={"version": 1, "relativistic_treatment": {"state": "known", "value": "none"}})
    assert met.applicability is A.applicable
    differs = assess(calc(), recipe={"version": 1, "relativistic_treatment": {"state": "known", "value": "x2c"}})
    assert differs.applicability is A.incompatible and codes(differs) == ["recipe_fact_differs:relativistic_treatment"]
    missing = assess(
        calc(declared=declaration(relativistic_treatment={"state": "unknown"})),
        recipe={"version": 1, "relativistic_treatment": {"state": "known", "value": "none"}},
    )
    assert missing.applicability is A.unresolved
    assert codes(missing) == ["recipe_fact_unestablished_for_request:relativistic_treatment"]


# -- diagnostics and findings ------------------------------------------------------------------------------


def test_scf_stability_blocks_only_when_the_request_requires_a_stable_reference():
    unstable = calc(scf_stability="unstable")
    assert assess(unstable).physically_eligible and "scf_wavefunction_unstable" in assess(unstable).advisory
    required = assess(unstable, require_stable_reference=True)
    assert required.blocking == ("scf_wavefunction_unstable",) and not required.physically_eligible
    assert assess(calc(), require_stable_reference=True).applicability is A.unresolved
    assert codes(assess(calc(), require_stable_reference=True)) == ["scf_stability_not_checked"]
    assert assess(calc(scf_stability="inconclusive"), require_stable_reference=True).applicability is A.unresolved
    assert assess(calc(scf_stability="stable"), require_stable_reference=True).physically_eligible
    # Never having run the analysis is not a contradiction of a request that does not need it.
    assert assess(calc(scf_stability=None)).physically_eligible


def test_an_automated_geometry_check_that_failed_is_attention_not_a_verdict():
    a = assess(calc(geometry_validation="fail"))
    assert a.physically_eligible and "geometry_validation_heuristic_failed" in a.advisory


def test_a_live_confirmed_invalidation_blocks_and_an_adjudication_that_clears_it_unblocks():
    bad = finding_facts("sfnd_1", kind="identity_incompatibility", subject_ref="calc_a")
    blocked = assess(calc(findings=(bad,)))
    assert blocked.blocking == ("finding_invalidates:identity_incompatibility:authorized_adjudication",)
    cleared = finding_facts(
        "sfnd_2", kind="adjudication", verdict="does_not_invalidate", supersedes="sfnd_1", subject_ref="calc_a"
    )
    assert assess(calc(findings=(bad, cleared))).physically_eligible
    # An adjudication that itself invalidates keeps the block, under its own authority.
    upheld = finding_facts("sfnd_3", kind="adjudication", verdict="invalidates", supersedes="sfnd_1", subject_ref="calc_a")
    assert assess(calc(findings=(bad, upheld))).blocking == ("finding_invalidates:adjudication:authorized_adjudication",)


def test_only_an_authorized_adjudication_about_the_same_subject_settles_a_finding():
    bad = finding_facts("sfnd_1", kind="identity_incompatibility", subject_ref="calc_a")
    blocked = ("finding_invalidates:identity_incompatibility:authorized_adjudication",)
    # A producer's own assertion cannot erase an authorized disproof by naming it.
    asserted = finding_facts(
        "sfnd_2", kind="adjudication", verdict="does_not_invalidate", authority="producer_assertion", supersedes="sfnd_1",
        subject_ref="calc_a",
    )
    assert assess(calc(findings=(bad, asserted))).blocking == blocked
    # A finding that is not an adjudication settles nothing, whatever it names.
    for kind in ("identity_incompatibility", "state_incompatibility", "path_incompatibility", "contradictory_characterization"):
        naming = finding_facts("sfnd_3", kind=kind, verdict="does_not_invalidate", supersedes="sfnd_1", subject_ref="calc_a")
        assert assess(calc(findings=(bad, naming))).blocking == blocked
    # An adjudication about another subject says nothing about this one.
    elsewhere = finding_facts(
        "sfnd_4", kind="adjudication", verdict="does_not_invalidate", supersedes="sfnd_1", subject_ref="calc_other"
    )
    assert assess(calc(findings=(bad, elsewhere))).blocking == blocked
    other_scope = finding_facts(
        "sfnd_5", kind="adjudication", verdict="does_not_invalidate", supersedes="sfnd_1", subject_ref="calc_a", scope="geometry"
    )
    assert assess(calc(findings=(bad, other_scope))).blocking == blocked
    # An adjudication this release cannot read settles nothing.
    future = finding_facts(
        "sfnd_6", kind="adjudication", verdict="does_not_invalidate", supersedes="sfnd_1", subject_ref="calc_a", version=2
    )
    assert assess(calc(findings=(bad, future))).blocking == blocked
    # A readable, authorized, same-subject adjudication does.
    good = finding_facts("sfnd_7", kind="adjudication", verdict="does_not_invalidate", supersedes="sfnd_1", subject_ref="calc_a")
    assert assess(calc(findings=(bad, good))).physically_eligible


def test_a_producer_assertion_of_no_problem_clears_nothing_and_an_unresolved_finding_leaves_the_unit_unresolved():
    bad = finding_facts("sfnd_1", subject_ref="calc_a")
    assertion = finding_facts("sfnd_9", verdict="does_not_invalidate", authority="producer_assertion", subject_ref="calc_a")
    assert assess(calc(findings=(bad, assertion))).blocking != ()
    open_question = finding_facts("sfnd_4", kind="state_incompatibility", verdict="unresolved", subject_ref="calc_a")
    a = assess(calc(findings=(open_question,)))
    assert a.applicability is A.unresolved and "finding_unresolved:state_incompatibility" in codes(a)


def test_a_finding_kind_or_version_this_release_does_not_read_is_disclosed_never_a_universal_failure():
    future = finding_facts("sfnd_5", version=2)
    a = assess(calc(findings=(future,)))
    assert a.physically_eligible and "finding_version_unsupported" in a.advisory
    odd = finding_facts("sfnd_6", kind="some_future_kind")
    b = assess(calc(findings=(odd,)))
    assert b.physically_eligible and "finding_kind_unsupported" in b.advisory


def test_a_dependency_cycle_is_unresolved():
    a = assess(calc(lineage_cyclic=True))
    assert a.applicability is A.unresolved and "dependency_cycle" in codes(a)


def test_curvature_facts_play_no_part_in_a_recorded_value():
    unit = calc(curvature=CurvatureFacts(has_freq_result=True, n_imag=3))
    assert assess(unit).physically_eligible
