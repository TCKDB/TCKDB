"""Determination-grain assessment: does a claimed basin or saddle have the evidence its claim needs, on its geometry?"""

from __future__ import annotations

import pytest

from app.services.selection_kernel import Applicability as A
from app.services.structure_selection.assessment import assess_determination
from app.services.structure_selection.models import (
    CurvatureFacts,
    EnergyFacts,
    EnergyScope,
    Grain,
    Intent,
    LineageEdge,
    Quantity,
    StructureRequest,
    ValidationClaim,
)
from tests.services.structure_selection._support import (
    G1,
    G2,
    calc,
    codes,
    det,
    finding_facts,
    freq,
    level,
    minimum_request,
    saddle_request,
    source,
    subject,
)

TS_SUBJECT = subject(kind="transition_state_entry", entry_ref="tse_a", stationary_point_kind=None, electronic_state_kind=None)


def units(*calcs):
    return {c.calculation_ref: c for c in calcs}


def opt(ref="calc_o", **kw):
    return calc(ref, type="opt", **kw)


def assess(d, *calcs, request=None, subj=None):
    return assess_determination(
        d, request=request or minimum_request(), subject=subj or subject(), calculations=units(*calcs)
    )


def happy(**freq_kw):
    return (opt(), calc("calc_a", type="sp"), freq("calc_f", **freq_kw))


# -- a validated minimum -------------------------------------------------------------------------------------


def test_a_determination_with_energy_optimisation_and_curvature_on_its_geometry_is_a_validated_minimum():
    a = assess(det(), *happy())
    assert a.applicability is A.applicable and a.physically_eligible
    assert a.energy is not None and a.energy.calculation_ref == "calc_a"
    assert a.energy.scope is EnergyScope.recorded_single_point and a.energy.geometry_ref == G1
    assert a.claim is not None and a.claim.supported and a.claim.claim == "local_minimum"
    assert a.claim.witness_refs == ("calc_f",)
    assert a.recipe is not None and a.recipe.cohort_key is not None


def test_curvature_at_another_geometry_is_not_borrowed_for_this_one():
    # The same entry has a frequency result, at a different geometry, that says "minimum".
    a = assess(det(sources=(source("energy", "calc_a"), source("curvature", "calc_f"))), calc("calc_a"), freq("calc_f", geometry=G2))
    assert a.applicability is A.unresolved
    assert "no_curvature_witness_on_the_evaluated_geometry" in codes(a)
    assert "curvature_on_other_geometry" in a.advisory
    assert a.claim is not None and not a.claim.supported


def test_a_frequency_that_found_imaginary_modes_contradicts_a_minimum_and_blocks_only_that_claim():
    stiff = curved(1, (1, -300.0, None), imag_freq=-300.0)
    a = assess(det(), opt(), calc("calc_a"), stiff)
    assert a.blocking == ("curvature_contradicts_claim",) and not a.physically_eligible
    assert a.applicability is A.applicable  # the energy question itself is unaffected
    # A request that claims no structure is not blocked by the same evidence.
    energy_only = assess(det(sources=(source("energy", "calc_a"),)), calc("calc_a"), request=minimum_request())
    assert energy_only.applicability is A.unresolved  # a validated minimum still needs its curvature declared


def test_an_unstated_imaginary_count_is_unresolved_never_zero():
    a = assess(det(), opt(), calc("calc_a"), freq("calc_f", n_imag=None))
    assert a.applicability is A.unresolved and "imaginary_mode_count_not_stated" in codes(a)


def test_a_stored_hessian_with_no_evaluated_curvature_is_a_witness_that_exists_but_does_not_certify():
    hessian_only = opt("calc_h", curvature=CurvatureFacts(has_hessian=True, hessian_geometry_ref=G1))
    d = det(sources=(source("energy", "calc_a"), source("curvature", "calc_h")))
    a = assess(d, calc("calc_a"), hessian_only)
    assert a.applicability is A.unresolved and "hessian_curvature_not_evaluated" in codes(a)


def test_a_hessian_carried_by_an_optimisation_is_found_on_its_output_geometry_regardless_of_job_type():
    carrier = opt("calc_o", curvature=CurvatureFacts(has_freq_result=True, n_imag=0))
    d = det(sources=(source("geometry_optimization", "calc_o"), source("energy", "calc_a"), source("curvature", "calc_o")))
    a = assess(d, carrier, calc("calc_a"))
    assert a.claim is not None and a.claim.supported and a.claim.witness_refs == ("calc_o",)


def test_the_entry_must_be_a_minimum_for_a_minimum_claim():
    a = assess(det(), *happy(), subj=subject(stationary_point_kind="transition_state"))
    assert a.blocking == ("curvature_contradicts_claim",)
    assert "entry_kind_is_not_a_minimum" in a.advisory
    assert assess(det(), *happy(), subj=subject(stationary_point_kind="vdw_complex")).physically_eligible


def test_a_single_point_with_a_lower_level_characterisation_states_the_surface_of_its_claim():
    lower = freq("calc_f", level=level("lot_low"))
    a = assess(det(), opt(), calc("calc_a"), lower)
    assert a.physically_eligible and a.claim is not None
    assert a.claim.surface_level_refs == ("lot_low",)
    assert "curvature_surface_differs_from_energy" in a.advisory


def test_two_curvature_witnesses_that_disagree_keep_the_conflict_instead_of_choosing():
    d = det(sources=(source("energy", "calc_a"), source("curvature", "calc_f1"), source("curvature", "calc_f2")))
    two = CurvatureFacts(has_freq_result=True, n_imag=2, imaginary_modes=((1, -300.0, None), (2, -200.0, None)))
    a = assess(d, calc("calc_a"), freq("calc_f1", n_imag=0), freq("calc_f2", curvature=two))
    assert a.applicability is A.unresolved and "curvature_witnesses_disagree" in codes(a)
    assert a.claim is not None and not a.claim.supported and a.claim.witness_refs == ("calc_f1", "calc_f2")


def test_evidence_anchored_to_another_observation_is_not_borrowed_by_a_basin_claim():
    d = det(target_kind="conformer_basin", conformer_observation_ref="co_a")
    mine = assess(d, opt(conformer_observation_ref="co_a"), calc("calc_a", conformer_observation_ref="co_a"),
                  freq("calc_f", conformer_observation_ref="co_a"))
    assert mine.physically_eligible
    other = assess(d, opt(), calc("calc_a"), freq("calc_f", conformer_observation_ref="co_b"))
    assert other.applicability is A.unresolved and codes(other) == ["source_anchored_to_another_observation"]
    # An unanchored calculation says nothing against the claim; only a known different anchor does.
    assert assess(d, opt(), calc("calc_a"), freq("calc_f")).physically_eligible


def test_an_alternative_characterisation_alone_is_unsupported_until_an_adapter_exists():
    d = det(sources=(source("energy", "calc_a"), source("alternative_characterization", "calc_f")))
    a = assess(d, calc("calc_a"), freq("calc_f"))
    assert a.applicability is A.unsupported and "alternative_characterization_not_supported" in codes(a)


# -- the energy of a determination --------------------------------------------------------------------------


def test_an_energy_computed_at_another_geometry_is_incompatible_and_an_unlinked_one_unresolved():
    other = assess(det(), opt(), calc("calc_a", input_geometry_refs=(G2,)), freq("calc_f"))
    assert other.applicability is A.incompatible and "energy_at_other_geometry" in codes(other)
    unlinked = assess(det(), opt(), calc("calc_a", input_geometry_refs=()), freq("calc_f"))
    assert unlinked.applicability is A.unresolved and "energy_geometry_not_established" in codes(unlinked)
    two = assess(det(), opt(), calc("calc_a", input_geometry_refs=(G1, G2)), freq("calc_f"))
    assert two.applicability is A.unresolved and "energy_geometry_ambiguous" in codes(two)


def test_a_determination_that_supplies_no_energy_or_another_one_cannot_answer_an_energy_request():
    none = assess(det(quantity=None), *happy())
    assert none.applicability is A.unresolved and "energy_not_stated" in codes(none)
    e0 = assess(det(quantity="zero_kelvin_energy"), *happy())
    assert e0.applicability is A.incompatible and "determination_supplies_other_quantity" in codes(e0)


def test_evidence_only_qualification_needs_no_energy():
    request = StructureRequest(
        Grain.conformer, Intent.qualify_evidence, quantity=None, validation_claim=ValidationClaim.local_minimum
    )
    a = assess(det(quantity=None, sources=(source("curvature", "calc_f"),)), freq("calc_f"), request=request)
    assert a.applicability is A.applicable and a.energy is None and a.claim is not None and a.claim.supported


def test_two_energy_sources_are_ambiguous_not_averaged_or_picked():
    d = det(sources=(source("energy", "calc_a"), source("energy", "calc_b"), source("curvature", "calc_f")))
    a = assess(d, calc("calc_a"), calc("calc_b", rank=2), freq("calc_f"))
    assert a.applicability is A.unresolved and "energy_source_ambiguous" in codes(a)


def test_an_unconverged_optimisation_cannot_support_an_optimised_minimum_but_keeps_its_recorded_value():
    unconverged = opt("calc_o", energy=EnergyFacts(opt_final_hartree=-76.9, opt_converged=False))
    d = det(sources=(source("geometry_optimization", "calc_o"), source("energy", "calc_o"), source("curvature", "calc_f")))
    a = assess(d, unconverged, freq("calc_f"))
    assert a.blocking == ("optimization_not_converged",) and not a.physically_eligible
    unknown = opt("calc_o", energy=EnergyFacts(opt_final_hartree=-76.9, opt_converged=None))
    b = assess(d, unknown, freq("calc_f"))
    assert b.applicability is A.unresolved and "optimization_convergence_unknown" in codes(b)


def test_an_energy_source_of_a_type_that_carries_no_energy_is_incompatible():
    d = det(sources=(source("energy", "calc_f"), source("curvature", "calc_f")))
    a = assess(d, freq("calc_f"))
    assert a.applicability is A.incompatible and "source_type_cannot_supply_an_energy" in codes(a)


def test_a_fixed_geometry_request_admits_only_the_determinations_evaluated_there():
    a = assess(det(evaluated_geometry_ref=G2), *happy(geometry=G2), request=minimum_request(geometry_ref=G1))
    assert a.applicability is A.incompatible and "evaluated_at_other_geometry" in codes(a)


def test_the_determinations_own_recipe_completes_the_calculations_declaration():
    from tests.services.structure_selection._support import declaration

    sparse = calc("calc_a", declared=declaration(relativistic_treatment={"state": "unknown"}))
    plain = assess(det(), opt(), sparse, freq("calc_f"))
    assert plain.recipe.cohort_key is None
    completed = assess(
        det(actual_recipe={"version": 1, "relativistic_treatment": {"state": "known", "value": "none"}}),
        opt(), sparse, freq("calc_f"),
    )
    assert completed.recipe.cohort_key is not None


# -- unavailable evidence ------------------------------------------------------------------------------------


def test_a_role_whose_calculation_is_not_available_reads_with_the_reason_it_was_given_and_no_ref():
    hidden = det(sources=(source("energy", None, unavailable="required_evidence_unavailable"), source("curvature", "calc_f")))
    a = assess(hidden, freq("calc_f"))
    assert a.applicability is A.unresolved and codes(a) == ["required_evidence_unavailable"]
    below = det(sources=(source("energy", "calc_a"), source("curvature", None, unavailable="source_below_review_floor")))
    b = assess(below, calc("calc_a"))
    assert b.applicability is A.unresolved and codes(b) == ["source_below_review_floor"]
    undeclared = det(sources=(source("curvature", "calc_f"),))
    assert codes(assess(undeclared, freq("calc_f"))) == ["energy_source_not_declared"]
    nocurv = det(sources=(source("energy", "calc_a"),))
    assert codes(assess(nocurv, calc("calc_a"))) == ["curvature_evidence_not_declared"]


def test_a_role_the_request_does_not_need_may_be_unavailable():
    request = StructureRequest(
        Grain.conformer, Intent.qualify_evidence, quantity=None, validation_claim=ValidationClaim.local_minimum
    )
    d = det(quantity=None, sources=(source("energy", None, unavailable="required_evidence_unavailable"), source("curvature", "calc_f")))
    assert assess(d, freq("calc_f"), request=request).applicability is A.applicable


# -- lineage -------------------------------------------------------------------------------------------------


def test_a_source_the_typed_edges_do_not_tie_to_the_optimisation_is_disclosed_and_one_they_do_is_not():
    unlinked = assess(det(), *happy())
    assert "source_not_linked_to_geometry_optimization:energy" in unlinked.advisory
    assert "source_not_linked_to_geometry_optimization:curvature" in unlinked.advisory
    linked = assess(
        det(),
        opt(),
        calc("calc_a", lineage=(LineageEdge("calc_a", "calc_o", "single_point_on"),)),
        freq("calc_f", lineage=(LineageEdge("calc_f", "calc_o", "freq_on"),)),
    )
    assert not [x for x in linked.advisory if x.startswith("source_not_linked")]
    # The edge never rescues a geometry that does not match.
    assert not assess(det(), opt(), calc("calc_a", input_geometry_refs=(G2,), lineage=(LineageEdge("calc_a", "calc_o", "single_point_on"),)), freq("calc_f")).physically_eligible


def test_a_dependency_cycle_makes_the_energy_source_unresolved():
    a = assess(det(), opt(), calc("calc_a", lineage_cyclic=True), freq("calc_f"))
    assert a.applicability is A.unresolved and "dependency_cycle" in codes(a)


# -- findings at their own subject and role ------------------------------------------------------------------


def test_a_failed_rerun_on_another_geometry_does_not_erase_a_valid_bundle_here():
    rerun = finding_facts("sfnd_1", kind="identity_incompatibility", scope="geometry", subject_ref=G2)
    a = assess(det(findings=(rerun,)), *happy())
    assert a.physically_eligible and "finding_on_unneeded_subject:identity_incompatibility" in a.advisory


def test_a_confirmed_disproof_of_this_target_cannot_be_evaded_by_choosing_the_older_pass():
    bad_geometry = finding_facts("sfnd_1", scope="geometry", subject_ref=G1)
    assert assess(det(findings=(bad_geometry,)), *happy()).blocking == (
        "finding_invalidates:identity_incompatibility:authorized_adjudication",
    )
    on_determination = finding_facts("sfnd_2", scope="determination", subject_ref="sdet_a", kind="path_incompatibility")
    assert assess(det(findings=(on_determination,)), *happy()).blocking != ()
    on_source = finding_facts("sfnd_3", scope="calculation", subject_ref="calc_a", kind="state_incompatibility")
    assert assess(det(findings=(on_source,)), *happy()).blocking != ()


def test_a_finding_on_a_source_the_request_does_not_need_is_disclosed_not_blocking():
    on_optimisation = finding_facts("sfnd_4", scope="calculation", subject_ref="calc_o")
    a = assess(det(findings=(on_optimisation,)), *happy())
    assert a.physically_eligible and "finding_on_unneeded_subject:identity_incompatibility" in a.advisory


def test_a_role_invalidation_blocks_only_the_role_it_names_and_only_when_that_role_is_needed():
    energy_role = finding_facts("sfnd_5", kind="role_invalidation", scope="calculation", subject_ref="calc_a", role="energy")
    assert assess(det(findings=(energy_role,)), *happy()).blocking == ("role_invalidated:energy:authorized_adjudication",)
    evidence_only = StructureRequest(
        Grain.conformer, Intent.qualify_evidence, quantity=None, validation_claim=ValidationClaim.local_minimum
    )
    a = assess(det(quantity=None, findings=(energy_role,)), *happy(), request=evidence_only)
    assert a.physically_eligible
    curvature_role = finding_facts("sfnd_6", kind="role_invalidation", scope="calculation", subject_ref="calc_f", role="curvature")
    assert assess(det(findings=(curvature_role,)), *happy(), request=evidence_only).blocking != ()


def test_a_superseded_finding_is_history():
    bad = finding_facts("sfnd_1", scope="determination", subject_ref="sdet_a")
    cleared = finding_facts("sfnd_2", kind="adjudication", verdict="does_not_invalidate", scope="determination", subject_ref="sdet_a", supersedes="sfnd_1")
    assert assess(det(findings=(bad, cleared)), *happy()).physically_eligible


# -- a saddle ------------------------------------------------------------------------------------------------


def ts_freq(ref="calc_f", **kw):
    n = kw.pop("n_imag", 1)
    cv = CurvatureFacts(
        has_freq_result=True,
        n_imag=n,
        reaction_coordinate_mode_index=kw.pop("rc", 1 if n and n > 1 else None),
        structural_flag=kw.pop("flag", None),
    )
    return freq(ref, curvature=cv, **kw)


def saddle(*extra_sources, quantity="electronic_energy", **kw):
    return det(
        owner_kind="transition_state_entry",
        target_kind="saddle_point",
        entry_kind=None,
        quantity=quantity,
        sources=(source("geometry_optimization", "calc_o"), source("energy", "calc_a"), source("curvature", "calc_f"), *extra_sources),
        **kw,
    )


def test_a_first_order_saddle_is_supported_by_one_imaginary_mode():
    a = assess(saddle(), opt(), calc("calc_a"), ts_freq(), request=saddle_request(), subj=TS_SUBJECT)
    assert a.physically_eligible and a.claim is not None and a.claim.claim == "first_order_saddle" and a.claim.supported


def test_no_imaginary_mode_is_not_a_saddle():
    a = assess(saddle(), opt(), calc("calc_a"), ts_freq(n_imag=0), request=saddle_request(), subj=TS_SUBJECT)
    assert a.blocking == ("curvature_contradicts_claim",) and "no_imaginary_mode" in a.advisory


@pytest.mark.parametrize(
    "flag, rc, claim, expected, treatment",
    [
        (False, 2, ValidationClaim.first_order_saddle, "supports", "extra_imaginary_modes_below_tau"),
        (True, 2, ValidationClaim.first_order_saddle, "unsupported", None),
        (None, 2, ValidationClaim.first_order_saddle, "unresolved", None),
        (False, None, ValidationClaim.first_order_saddle, "unresolved", None),
        (True, 2, ValidationClaim.higher_order_saddle, "supports", "local_higher_order_saddle"),
        (False, 2, ValidationClaim.higher_order_saddle, "unresolved", None),
        (None, 2, ValidationClaim.higher_order_saddle, "unresolved", None),
    ],
)
def test_extra_imaginary_modes_are_judged_by_the_owners_persisted_treatment_not_by_their_count(flag, rc, claim, expected, treatment):
    a = assess(
        saddle(), opt(), calc("calc_a"), ts_freq(n_imag=3, flag=flag, rc=rc),
        request=saddle_request(validation_claim=claim), subj=TS_SUBJECT,
    )
    if expected == "supports":
        assert a.physically_eligible and a.claim.supported and a.claim.treatment == treatment
        if claim is ValidationClaim.higher_order_saddle:
            assert "not_first_order_tst_suitable" in a.advisory
    elif expected == "unsupported":
        assert a.applicability is A.unsupported and "higher_order_saddle_flagged" in codes(a)
    else:
        assert a.applicability is A.unresolved and not a.physically_eligible


def test_a_first_order_saddle_contradicts_a_higher_order_claim():
    a = assess(
        saddle(), opt(), calc("calc_a"), ts_freq(n_imag=1),
        request=saddle_request(validation_claim=ValidationClaim.higher_order_saddle), subj=TS_SUBJECT,
    )
    assert a.blocking == ("curvature_contradicts_claim",)


def evidence(passed=True, calculation_ref="calc_irc", geometry_ref=G1, kind="irc"):
    return {"kind": kind, "passed": passed, "calculation_ref": calculation_ref, "geometry_ref": geometry_ref}


def with_irc(**changes):
    return saddle(source("connectivity", "calc_irc"), validation_evidence=(evidence(),), **changes)


def irc_calc():
    return calc("calc_irc", type="opt", energy=None, output_geometry_refs=(G1,))


def test_reactive_connectivity_is_a_separate_claim_that_needs_path_evidence_on_this_geometry():
    request = saddle_request(require_connectivity=True)
    base = (opt(), calc("calc_a"), ts_freq(), irc_calc())
    ok = assess(with_irc(), *base, request=request, subj=TS_SUBJECT)
    assert ok.physically_eligible and ok.claim.claim == "first_order_saddle+reactive_connectivity"
    assert ok.claim.witness_refs == ("calc_f", "calc_irc")
    # A local saddle is supported without a path, and says nothing about connectivity.
    local = assess(saddle(), opt(), calc("calc_a"), ts_freq(), request=saddle_request(), subj=TS_SUBJECT)
    assert local.physically_eligible and local.claim.claim == "first_order_saddle"
    # Required but absent: unresolved, not a failure.
    missing = assess(saddle(), opt(), calc("calc_a"), ts_freq(), request=request, subj=TS_SUBJECT)
    assert missing.applicability is A.unresolved and "connectivity_not_established" in codes(missing)
    refuted = assess(
        saddle(source("connectivity", "calc_irc"), validation_evidence=(evidence(passed=False),)), *base, request=request, subj=TS_SUBJECT
    )
    assert refuted.blocking == ("reactive_connectivity_refuted",)
    elsewhere = assess(
        saddle(source("connectivity", "calc_irc"), validation_evidence=(evidence(geometry_ref=G2),)), *base, request=request, subj=TS_SUBJECT
    )
    assert elsewhere.applicability is A.unresolved
    assert "connectivity_evidence_not_bound_to_the_evaluated_geometry" in codes(elsewhere)
    unbound = assess(
        saddle(source("connectivity", "calc_irc"), validation_evidence=(evidence(geometry_ref=None),)), *base, request=request, subj=TS_SUBJECT
    )
    assert unbound.applicability is A.unresolved
    foreign = assess(
        saddle(source("connectivity", "calc_irc"), validation_evidence=(evidence(calculation_ref="calc_other"),)),
        *base, request=request, subj=TS_SUBJECT,
    )
    assert "connectivity_not_established" in codes(foreign)


def test_connectivity_alone_can_be_qualified_without_an_energy_or_a_curvature_claim():
    request = StructureRequest(
        Grain.transition_state, Intent.qualify_evidence, quantity=None, validation_claim=ValidationClaim.reactive_connectivity
    )
    d = det(
        owner_kind="transition_state_entry", target_kind="saddle_point", entry_kind=None, quantity=None,
        sources=(source("connectivity", "calc_irc"),), validation_evidence=(evidence(),),
    )
    a = assess(d, irc_calc(), request=request, subj=TS_SUBJECT)
    assert a.physically_eligible and a.claim.claim == "reactive_connectivity" and a.energy is None


def test_unavailable_connectivity_evidence_reads_generically():
    d = saddle(source("connectivity", None, unavailable="required_evidence_unavailable"))
    a = assess(d, opt(), calc("calc_a"), ts_freq(), request=saddle_request(require_connectivity=True), subj=TS_SUBJECT)
    assert a.applicability is A.unresolved and "required_evidence_unavailable" in codes(a)


def test_a_geometry_validation_heuristic_on_a_source_does_not_block_a_determination():
    a = assess(det(), opt(), calc("calc_a", geometry_validation="fail"), freq("calc_f"))
    assert a.physically_eligible and "geometry_validation_heuristic_failed" in a.advisory


def test_the_electronic_energy_request_never_falls_back_when_the_energy_source_is_a_composite_with_only_an_e0():
    e = EnergyFacts(composite_assembly="program_run", composite_e0_hartree=-76.38, composite_recipe_zpe_hartree=0.02)
    source_calc = calc("calc_a", type="composite", energy=e)
    d = det(sources=(source("energy", "calc_a"), source("curvature", "calc_f")))
    a = assess(d, source_calc, freq("calc_f"))
    assert a.applicability is A.unresolved and "energy_not_deposited" in codes(a)
    # Asked for E0 with a determination that declares it, the same composite answers.
    e0 = assess(
        det(quantity="zero_kelvin_energy", sources=(source("energy", "calc_a"), source("curvature", "calc_f"))),
        source_calc, freq("calc_f"), request=minimum_request(quantity=Quantity.zero_kelvin_energy),
    )
    assert e0.physically_eligible and e0.energy.scope is EnergyScope.composite_zero_kelvin


# -- curvature is judged by the shared stationary-point owner ------------------------------------------------


def curved(n_imag, *modes, rc=None, flag=None, tau=None, tau_basis=None, imag_freq=None):
    """A frequency result carrying its stored imaginary modes ``(index, cm-1, disposition)``."""
    return freq(
        "calc_f",
        curvature=CurvatureFacts(
            has_freq_result=True,
            n_imag=n_imag,
            reaction_coordinate_mode_index=rc,
            structural_flag=flag,
            tau_cm1=tau,
            tau_basis=tau_basis,
            imag_freq_cm1=imag_freq,
            imaginary_modes=tuple(modes),
        ),
    )


def minimum_verdict(n, *modes, kind="minimum", imag_freq=None, tau=None, tau_basis=None):
    subj = subject(stationary_point_kind=kind)
    return assess(
        det(), opt(), calc("calc_a"),
        curved(n, *modes, imag_freq=imag_freq, tau=tau, tau_basis=tau_basis), subj=subj,
    )


@pytest.mark.parametrize("kind", ["minimum", "vdw_complex"])
def test_a_minimum_claim_is_supported_only_by_no_imaginary_mode_or_by_every_one_being_soft(kind):
    none = minimum_verdict(0, kind=kind)
    assert none.physically_eligible and none.claim.supported
    # Every imaginary mode below tau (the default tau is 50 cm-1): supported, and the owner's warning is an advisory.
    soft = minimum_verdict(1, (1, -12.0, None), kind=kind, imag_freq=-12.0)
    assert soft.physically_eligible and soft.claim.supported
    assert "stationary_point_finding:n_imag_contradicts_minimum" in soft.advisory and "imaginary_modes_below_tau" in soft.advisory
    # A stiff mode is real negative curvature (ADR 0012): the owner's warning tier is not a verdict that this is a minimum.
    stiff = minimum_verdict(1, (1, -420.0, None), kind=kind, imag_freq=-420.0)
    assert stiff.blocking == ("curvature_contradicts_claim",) and not stiff.physically_eligible
    assert "imaginary_mode_at_or_above_tau" in stiff.advisory
    far = minimum_verdict(1, (1, -500.0, None), kind=kind, imag_freq=-500.0)
    assert far.blocking == ("curvature_contradicts_claim",)
    # A third-order saddle is not a minimum even when every mode is individually smaller than a reaction coordinate.
    third = minimum_verdict(3, (1, -300.0, None), (2, -200.0, None), (3, -150.0, None), kind=kind)
    assert third.blocking == ("curvature_contradicts_claim",)
    # Imaginary modes are judged by magnitude, not counted (ADR 0012): several modes that are all below tau do not
    # contradict a minimum, and the owner's count-based findings are advisories only.
    several = minimum_verdict(2, (1, -12.0, None), (2, -9.0, None), kind=kind)
    assert several.physically_eligible and several.claim.supported
    # One stiff mode among soft ones is decided curvature.
    mixed = minimum_verdict(2, (1, -12.0, None), (2, -90.0, None), kind=kind)
    assert mixed.blocking == ("curvature_contradicts_claim",)


def test_the_same_soft_modes_give_the_same_verdict_for_every_minimum_kind():
    modes = ((1, -12.0, None), (2, -9.0, None), (3, -20.0, None))
    verdicts = {kind: minimum_verdict(3, *modes, kind=kind) for kind in ("minimum", "vdw_complex")}
    shapes = {
        kind: (v.applicability, v.blocking, v.claim.supported, "imaginary_modes_below_tau" in v.advisory)
        for kind, v in verdicts.items()
    }
    assert shapes["minimum"] == shapes["vdw_complex"] == (A.applicable, (), True, True)
    # The two owners differ in what they say about the count; the advisories carry what each said.
    assert verdicts["vdw_complex"].advisory != verdicts["minimum"].advisory


@pytest.mark.parametrize("kind", ["minimum", "vdw_complex"])
def test_imaginary_modes_with_no_stored_magnitude_leave_a_minimum_claim_unresolved(kind):
    a = minimum_verdict(1, kind=kind)  # counted, but no magnitude stored
    assert a.applicability is A.unresolved and "imaginary_mode_magnitude_not_stored" in codes(a)
    assert not a.physically_eligible and not a.blocking
    # Fewer modes stored than counted: the missing ones are not assumed soft.
    short = minimum_verdict(2, (1, -12.0, None), kind=kind)
    assert short.applicability is A.unresolved and "imaginary_mode_magnitude_not_stored" in codes(short)
    # A scalar magnitude judges a single imaginary mode and nothing more.
    scalar_only = minimum_verdict(2, imag_freq=-12.0, kind=kind)
    assert scalar_only.applicability is A.unresolved


def test_the_soft_stiff_boundary_is_the_stored_tau_inclusive():
    just_below = minimum_verdict(1, (1, -29.9, None), tau=30.0, tau_basis="analytic_default")
    assert just_below.physically_eligible
    at_tau = minimum_verdict(1, (1, -30.0, None), tau=30.0, tau_basis="analytic_default")
    assert at_tau.blocking == ("curvature_contradicts_claim",)  # "at or above tau" is decided curvature
    # The same -40 cm-1 is noise at a loose protocol and negative curvature at a tight one.
    assert minimum_verdict(1, (1, -40.0, None), tau=80.0, tau_basis="finite_difference_energy").physically_eligible
    assert minimum_verdict(1, (1, -40.0, None), tau=15.0, tau_basis="analytic_tight").blocking != ()


def test_a_transition_state_is_judged_from_its_stored_modes_the_designated_coordinate_and_tau():
    def run(extra, **kw):
        modes = ((1, -1500.0, None), (2, extra, kw.pop("disposition", "unassigned")))
        return assess(saddle(), opt(), calc("calc_a"), curved(2, *modes, rc=1, **kw), request=saddle_request(), subj=TS_SUBJECT)

    below = run(-20.0)
    assert below.physically_eligible and below.claim.treatment == "extra_imaginary_modes_below_tau"
    above = run(-300.0)
    assert above.applicability is A.unsupported and "higher_order_saddle_flagged" in codes(above)
    # Tau is the stored one, not a constant: -20 cm-1 is noise at the default and real at a tight analytic protocol.
    tight = run(-20.0, tau=15.0, tau_basis="analytic_tight")
    assert tight.applicability is A.unsupported
    loose = run(-60.0, tau=80.0, tau_basis="finite_difference_energy")
    assert loose.physically_eligible
    # An extra mode at least as stiff as the designated coordinate, with no declared disposition, is ambiguous.
    ambiguous = run(-1600.0, disposition=None)
    assert ambiguous.applicability is A.unresolved and "reaction_coordinate_not_established" in codes(ambiguous)


def test_extra_modes_with_no_designated_coordinate_are_unresolved_and_a_mode_free_saddle_is_not_one():
    modes = ((1, -1500.0, None), (2, -20.0, "unassigned"))
    undesignated = assess(saddle(), opt(), calc("calc_a"), curved(2, *modes), request=saddle_request(), subj=TS_SUBJECT)
    assert undesignated.applicability is A.unresolved and "reaction_coordinate_not_established" in codes(undesignated)
    none = assess(saddle(), opt(), calc("calc_a"), curved(0), request=saddle_request(), subj=TS_SUBJECT)
    assert none.blocking == ("curvature_contradicts_claim",) and "no_imaginary_mode" in none.advisory


def test_a_higher_order_claim_needs_a_flagged_extra_mode_from_the_owner():
    request = saddle_request(validation_claim=ValidationClaim.higher_order_saddle)
    flagged = assess(
        saddle(), opt(), calc("calc_a"), curved(2, (1, -1500.0, None), (2, -300.0, "torsion"), rc=1), request=request, subj=TS_SUBJECT
    )
    assert flagged.physically_eligible and flagged.claim.treatment == "local_higher_order_saddle"
    assert "not_first_order_tst_suitable" in flagged.advisory
    quiet = assess(
        saddle(), opt(), calc("calc_a"), curved(2, (1, -1500.0, None), (2, -20.0, "torsion"), rc=1), request=request, subj=TS_SUBJECT
    )
    assert quiet.applicability is A.unresolved and "extra_imaginary_modes_below_tau" in codes(quiet)
