"""The kinetics applicability assessor: an unstated fact is unknown, never a default.

Every adversarial case from the council's list is one row: a record that differs from the
fully-qualifying baseline in one fact (or a request that asks a different question), the verdict,
and the reason that must appear. The headline property is the first test: removing a statement
never *improves* a verdict.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.kinetics_declarations import KineticsCoefficientBasis as Basis

from app.db.models.common import KineticsDeterminationTargetKind as TargetKind
from app.db.models.common import KineticsDirection
from app.services.kinetics_selection.assessment import assess_candidate
from app.services.kinetics_selection.models import PressureKind, PressureRequest, TargetRequest
from app.services.selection_kernel import Applicability as A
from tests.services.kinetics_selection._support import (
    applicability,
    collider,
    determination,
    finite,
    norm,
    request,
)


def verdict(record, req=None):
    return assess_candidate(record, request=req or request(), evidence=None)


def codes(a):
    return {r.code for r in a.reasons}


def test_the_baseline_record_answers_the_baseline_request():
    a = verdict(norm())
    assert a.applicability is A.applicable and a.reasons == () and a.physically_eligible


# -- unstated is unknown ------------------------------------------------------------------------

UNSTATED = [
    ("determination_not_declared", {"determination": None, "representation_role": None}),
    ("direction_not_recorded", {"direction": None}),
    ("applicability_not_declared", {"applicability_state": "absent", "applicability": None}),
    ("applicability_unreadable", {"applicability_state": "unreadable", "applicability": None}),
    ("phase_not_declared", {"applicability": applicability(phase=None)}),
    ("observable_not_declared", {"applicability": applicability(observable=None)}),
    ("coefficient_basis_not_declared", {"applicability": applicability(coefficient_basis=None)}),
    ("pressure_dependence_not_declared", {"applicability": applicability(pressure_dependence=None)}),
    ("a_units_not_recorded", {"a_units": None}),
    ("temperature_domain_not_recorded", {"tmin_k": None}),
    ("temperature_domain_not_recorded", {"tmax_k": None}),
    ("degeneracy_convention_unknown", {"degeneracy": 2.0, "degeneracy_convention": "unknown"}),
    ("rate_progress_convention_not_declared", {"reactant_stoichiometries": (2,)}),
]


@pytest.mark.parametrize("code,changes", UNSTATED, ids=[f"{c}-{i}" for i, (c, _) in enumerate(UNSTATED)])
def test_a_missing_statement_makes_the_record_unresolved_and_never_eligible(code, changes):
    a = verdict(norm(**changes))
    assert a.applicability is A.unresolved, a.reasons
    assert code in codes(a)
    assert not a.physically_eligible


def test_saying_less_never_earns_an_edge_over_saying_more():
    complete = verdict(norm())
    for _, changes in UNSTATED:
        less = verdict(norm(**changes))
        assert complete.physically_eligible and not less.physically_eligible


def test_a_null_pressure_context_is_not_established_pressure_independence():
    a = verdict(norm(pressure_context=None, applicability=applicability(pressure_dependence=None)))
    assert a.applicability is A.unresolved and "pressure_dependence_not_declared" in codes(a)


# -- direction, target, representation ----------------------------------------------------------


def test_reverse_request_does_not_take_a_forward_record_and_a_net_rate_is_unsupported():
    reverse = request(direction=KineticsDirection.reverse)
    a = verdict(norm(), reverse)
    assert a.applicability is A.incompatible and {"direction_mismatch", "determination_direction_mismatch"} <= codes(a)
    assert verdict(norm(direction="net")).applicability is A.unsupported
    assert "net_rate_unsupported" in codes(verdict(norm(direction="net")))


def test_an_additive_component_is_unsupported_for_a_total_rate():
    a = verdict(norm(representation_role="additive_component"))
    assert a.applicability is A.unsupported and "additive_component_not_total_rate" in codes(a)


def test_a_channel_determination_does_not_answer_a_whole_reaction_request_and_vice_versa():
    channel = determination(target_kind="resolved_channel", transition_state_entry_ref="tse_1")
    assert "target_mismatch" in codes(verdict(norm(determination=channel, applicability=applicability(scope="resolved_channel"))))
    ts_request = request(target=TargetRequest(TargetKind.resolved_channel, transition_state_entry_ref="tse_1"))
    assert "target_mismatch" in codes(verdict(norm(), ts_request))


def test_a_resolved_channel_must_be_the_same_channel():
    ts_request = request(target=TargetRequest(TargetKind.resolved_channel, transition_state_entry_ref="tse_1"))
    same = norm(
        determination=determination(target_kind="resolved_channel", transition_state_entry_ref="tse_1"),
        applicability=applicability(scope="resolved_channel"),
    )
    other = norm(
        determination=determination(target_kind="resolved_channel", transition_state_entry_ref="tse_2"),
        applicability=applicability(scope="resolved_channel"),
    )
    assert verdict(same, ts_request).applicability is A.applicable
    assert "target_mismatch" in codes(verdict(other, ts_request))
    net_request = request(target=TargetRequest(TargetKind.resolved_channel, network_ref="net_1", channel_key="k1"))
    net_ok = norm(
        determination=determination(target_kind="resolved_channel", network_ref="net_1", channel_key="k1"),
        applicability=applicability(scope="resolved_channel"),
    )
    net_other = norm(
        determination=determination(target_kind="resolved_channel", network_ref="net_1", channel_key="k2"),
        applicability=applicability(scope="resolved_channel"),
    )
    assert verdict(net_ok, net_request).applicability is A.applicable
    assert "target_mismatch" in codes(verdict(net_other, net_request))


# -- the declared applicability against the request ---------------------------------------------


@pytest.mark.parametrize(
    "block,code,expected",
    [
        ({"phase": "liquid"}, "phase_mismatch", A.incompatible),
        ({"observable": "rate_of_progress"}, "observable_unsupported:rate_of_progress", A.unsupported),
        ({"observable": "effective_global_law"}, "observable_unsupported:effective_global_law", A.unsupported),
        ({"scope": "resolved_channel"}, "scope_mismatch", A.incompatible),
        ({"coefficient_basis": "third_body_kernel"}, "coefficient_basis_mismatch", A.incompatible),
        ({"reaction_order": 1}, "reaction_order_units_mismatch", A.incompatible),
    ],
)
def test_a_declared_claim_that_contradicts_the_request_or_the_units(block, code, expected):
    a = verdict(norm(applicability=applicability(**block)))
    assert a.applicability is expected and code in codes(a)


def test_coefficient_versus_reactant_loss_convention_is_required_for_a_squared_reactant():
    needs = norm(reactant_stoichiometries=(2,), applicability=applicability(rate_progress_convention="reactant_loss"))
    assert verdict(needs).applicability is A.applicable
    assert verdict(norm(reactant_stoichiometries=(1, 1))).applicability is A.applicable


def test_a_third_body_kernel_is_not_an_effective_coefficient_and_the_form_must_match_the_basis():
    kernel_request = request(coefficient_basis=Basis.third_body_kernel)
    kernel = norm(is_third_body=True, a_units="cm6_molecule2_s", applicability=applicability(coefficient_basis="third_body_kernel", reaction_order=3))
    assert verdict(kernel, kernel_request).applicability is A.applicable
    assert "coefficient_basis_mismatch" in codes(verdict(kernel))  # elementary request, kernel record
    assert "third_body_form_contradicts_basis" in codes(
        verdict(norm(is_third_body=True), request())
    )  # third-body form declared as an elementary coefficient
    assert "third_body_form_contradicts_basis" in codes(
        verdict(norm(applicability=applicability(coefficient_basis="third_body_kernel")), kernel_request)
    )


def test_degeneracy_is_disclosed_but_never_changes_the_stored_expression():
    applied = verdict(norm(degeneracy=2.0, degeneracy_convention="already_applied"))
    assert applied.applicability is A.applicable and "degeneracy_already_applied" in applied.advisory
    assert "degeneracy_not_applied" in codes(verdict(norm(degeneracy=2.0, degeneracy_convention="not_applied")))
    assert verdict(norm(degeneracy=1.0, degeneracy_convention="unknown")).applicability is A.applicable


def test_negative_fitted_terms_are_not_rejected_for_their_sign():
    a = verdict(norm(a=-3.0e-12))
    assert a.applicability is A.applicable
    multi = norm(model_kind="multi_arrhenius", a=None, a_units=None, arrhenius_terms=2)
    assert verdict(multi).applicability is A.applicable


# -- temperature coverage ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tmin,tmax,expected",
    [(300, 2000, A.applicable), (500, 1500, A.applicable), (600, 2000, A.incompatible),
     (300, 1400, A.incompatible), (1600, 2000, A.incompatible), (100, 200, A.incompatible)],
)
def test_the_whole_requested_temperature_window_must_be_covered(tmin, tmax, expected):
    # Partial, disjoint and overlapping-but-short bounds are not coverage.
    a = verdict(norm(tmin_k=float(tmin), tmax_k=float(tmax)))
    assert a.applicability is expected
    if expected is A.incompatible:
        assert "temperature_range_not_covered" in codes(a)


def test_a_point_request_is_covered_by_a_range_that_contains_it():
    assert verdict(norm(), request(temperature_min_k=700.0, temperature_max_k=700.0)).applicability is A.applicable


def test_chebyshev_temperature_bounds_intersect_with_the_record_bounds():
    ch = {"tmin_k": 600.0, "tmax_k": 1000.0, "pmin_bar": 0.1, "pmax_bar": 100.0, "n_temperature": 2, "n_pressure": 2, "has_coefficients": True}
    record = norm(model_kind="chebyshev", a=None, a_units=None, chebyshev=ch, applicability=applicability(pressure_dependence="pressure_dependent"))
    assert "temperature_range_not_covered" in codes(verdict(record, request(pressure=finite(1.0), collider=collider("sp_n2"))))


# -- pressure ------------------------------------------------------------------------------------


def hpl_record(**changes):
    return norm(pressure_context="high_p_limit", applicability=applicability(pressure_dependence="high_pressure_limit"), **changes)


def test_finite_pressure_versus_the_high_pressure_limit_and_independence():
    hpl_request = request(pressure=PressureRequest(PressureKind.high_pressure_limit))
    assert verdict(hpl_record(), hpl_request).applicability is A.applicable
    assert "pressure_dependence_mismatch" in codes(verdict(hpl_record()))  # independent request, HPL record
    assert "pressure_dependence_mismatch" in codes(verdict(norm(), hpl_request))  # HPL request, independent record
    # No automatic finite-pressure substitution.
    assert "pressure_dependence_mismatch" in codes(verdict(hpl_record(), request(pressure=finite(1.0), collider=collider("sp_n2"))))


def fixed_record(bar=1.0, **block):
    return norm(
        pressure_context="apparent_at_pressure", pressure_bar=bar,
        applicability=applicability(pressure_dependence="fixed_pressure", collider_kind="specified_collider",
                                    colliders=[{"species_ref": "sp_n2", "mole_fraction": None}], **block),
    )


def test_a_fixed_pressure_fit_answers_only_its_own_point_pressure():
    n2 = collider("sp_n2")
    assert verdict(fixed_record(), request(pressure=finite(1.0), collider=n2)).applicability is A.applicable
    assert verdict(fixed_record(1.0 + 1e-12), request(pressure=finite(1.0), collider=n2)).applicability is A.applicable
    assert "fixed_pressure_mismatch" in codes(verdict(fixed_record(2.0), request(pressure=finite(1.0), collider=n2)))
    assert "fixed_pressure_mismatch" in codes(verdict(fixed_record(), request(pressure=finite(0.5, 2.0), collider=n2)))
    # A window that starts at the record's own pressure is still a range, not that point.
    assert "fixed_pressure_mismatch" in codes(verdict(fixed_record(), request(pressure=finite(1.0, 2.0), collider=n2)))
    assert "fixed_pressure_not_recorded" in codes(verdict(fixed_record(bar=None), request(pressure=finite(1.0), collider=n2)))


def plog_record(pressures=(0.1, 1.0, 10.0), **block):
    return norm(
        model_kind="plog", a=None, a_units=None, plog_pressures_bar=tuple(pressures),
        applicability=applicability(pressure_dependence="pressure_dependent", collider_kind="not_dependent", **block),
    )


def test_plog_covers_only_the_whole_requested_envelope_within_its_anchors():
    n2 = collider("sp_n2")
    assert verdict(plog_record(), request(pressure=finite(0.5, 5.0), collider=n2)).applicability is A.applicable
    assert verdict(plog_record(), request(pressure=finite(0.1, 10.0), collider=n2)).applicability is A.applicable
    for low, high in ((0.05, 5.0), (0.5, 20.0), (20.0, 30.0)):
        a = verdict(plog_record(), request(pressure=finite(low, high), collider=n2))
        assert a.applicability is A.incompatible and "pressure_range_not_covered" in codes(a)


def test_same_pressure_plog_terms_are_one_representation_and_still_cover():
    record = plog_record(pressures=(0.1, 1.0, 1.0, 10.0))
    assert verdict(record, request(pressure=finite(1.0), collider=collider("sp_n2"))).applicability is A.applicable


def test_a_declared_validity_domain_narrows_what_the_table_supports():
    record = plog_record(pressure_domain_min_bar=0.5, pressure_domain_max_bar=2.0)
    n2 = collider("sp_n2")
    assert verdict(record, request(pressure=finite(1.0), collider=n2)).applicability is A.applicable
    assert "pressure_range_not_covered" in codes(verdict(record, request(pressure=finite(0.2, 1.0), collider=n2)))
    assert "pressure_range_not_covered" in codes(verdict(record, request(pressure=finite(1.0, 5.0), collider=n2)))


def chebyshev_record(pmin=0.1, pmax=100.0, **block):
    ch = {"tmin_k": 300.0, "tmax_k": 2000.0, "pmin_bar": pmin, "pmax_bar": pmax, "n_temperature": 2, "n_pressure": 2, "has_coefficients": True}
    return norm(
        model_kind="chebyshev", a=None, a_units=None, chebyshev=ch,
        applicability=applicability(pressure_dependence="pressure_dependent", collider_kind="not_dependent", **block),
    )


def test_chebyshev_needs_recorded_pressure_bounds_and_covers_only_inside_them():
    n2 = collider("sp_n2")
    assert verdict(chebyshev_record(), request(pressure=finite(1.0, 50.0), collider=n2)).applicability is A.applicable
    assert "pressure_range_not_covered" in codes(verdict(chebyshev_record(), request(pressure=finite(1.0, 500.0), collider=n2)))
    missing = verdict(chebyshev_record(pmin=None), request(pressure=finite(1.0), collider=n2))
    assert missing.applicability is A.unresolved and "pressure_domain_not_recorded" in codes(missing)


def test_a_falloff_fit_needs_a_declared_pressure_domain_and_complete_content():
    n2 = collider("sp_n2")
    base = {"model_kind": "troe", "has_falloff": True, "pressure_context": "pressure_dependent"}
    undeclared = norm(**base, applicability=applicability(pressure_dependence="pressure_dependent", collider_kind="not_dependent"))
    assert verdict(undeclared, request(pressure=finite(1.0), collider=n2)).applicability is A.unresolved
    assert "pressure_domain_not_declared" in codes(verdict(undeclared, request(pressure=finite(1.0), collider=n2)))
    declared = norm(**base, applicability=applicability(
        pressure_dependence="pressure_dependent", collider_kind="not_dependent",
        pressure_domain_min_bar=0.01, pressure_domain_max_bar=100.0))
    assert verdict(declared, request(pressure=finite(1.0), collider=n2)).applicability is A.applicable
    assert "pressure_range_not_covered" in codes(verdict(declared, request(pressure=finite(1.0, 1000.0), collider=n2)))
    incomplete = norm(**{**base, "has_falloff": False}, applicability=applicability(pressure_dependence="pressure_dependent", collider_kind="not_dependent", pressure_domain_min_bar=0.01, pressure_domain_max_bar=100.0))
    assert "representation_content_missing" in codes(verdict(incomplete, request(pressure=finite(1.0), collider=n2)))


@pytest.mark.parametrize(
    "changes",
    [{"model_kind": "plog", "plog_pressures_bar": ()}, {"model_kind": "chebyshev", "chebyshev": None},
     {"model_kind": "multi_arrhenius", "arrhenius_terms": 0}, {"model_kind": "modified_arrhenius", "a": None},
     {"model_kind": "troe", "has_falloff": False}],
)
def test_a_representation_without_its_content_is_incompatible(changes):
    a = verdict(norm(**changes))
    assert a.applicability is A.incompatible and "representation_content_missing" in codes(a)


def test_a_network_linked_fit_keeps_its_solve_and_must_match_its_channel():
    n2 = collider("sp_n2")
    linked = plog_record()
    linked = norm(
        **{**linked.__dict__, "network_channel_ref": "net_1/k1", "network_solve_ref": "solve_1",
           "determination": determination(target_kind="resolved_channel", network_ref="net_1", channel_key="k1")},
    )
    channel = TargetRequest(TargetKind.resolved_channel, network_ref="net_1", channel_key="k1")
    scope = applicability(pressure_dependence="pressure_dependent", collider_kind="not_dependent", scope="resolved_channel")
    linked = norm(**{**linked.__dict__, "applicability": scope})
    a = verdict(linked, request(target=channel, pressure=finite(1.0), collider=n2))
    assert a.applicability is A.applicable and "network_solve:solve_1" in a.advisory
    wrong = norm(**{**linked.__dict__, "network_channel_ref": "net_1/other"})
    assert "network_channel_mismatch" in codes(verdict(wrong, request(target=channel, pressure=finite(1.0), collider=n2)))


# -- collider and composition --------------------------------------------------------------------


def specified(ref):
    return {"collider_kind": "specified_collider", "colliders": [{"species_ref": ref, "mole_fraction": None}]}


def mixture(**fractions):
    return {
        "collider_kind": "fixed_mixture",
        "colliders": [{"species_ref": r, "mole_fraction": f} for r, f in fractions.items()],
    }


def test_n2_versus_he_and_unstated_collider():
    req_n2 = request(pressure=finite(1.0), collider=collider("sp_n2"))
    assert verdict(fixed_record(), req_n2).applicability is A.applicable
    he = norm(**{**fixed_record().__dict__, "applicability": applicability(pressure_dependence="fixed_pressure", **specified("sp_he"))})
    assert "collider_mismatch" in codes(verdict(he, req_n2))
    undeclared = norm(**{**fixed_record().__dict__, "applicability": applicability(pressure_dependence="fixed_pressure")})
    assert verdict(undeclared, req_n2).applicability is A.unresolved and "collider_not_declared" in codes(verdict(undeclared, req_n2))


def test_dry_versus_humid_air_is_a_mixture_match_to_the_stated_fractions():
    dry = {"sp_n2": 0.79, "sp_o2": 0.21}
    record = norm(**{**fixed_record().__dict__, "applicability": applicability(pressure_dependence="fixed_pressure", **mixture(**dry))})
    exact = request(pressure=finite(1.0), collider=collider("sp_n2", "sp_o2", fractions=(0.79, 0.21)))
    humid = request(pressure=finite(1.0), collider=collider("sp_n2", "sp_o2", "sp_h2o", fractions=(0.77, 0.2, 0.03)))
    shifted = request(pressure=finite(1.0), collider=collider("sp_n2", "sp_o2", fractions=(0.78, 0.22)))
    assert verdict(record, exact).applicability is A.applicable
    assert "collider_mismatch" in codes(verdict(record, humid))
    assert "collider_mismatch" in codes(verdict(record, shifted))
    half = norm(**{**fixed_record().__dict__, "applicability": applicability(pressure_dependence="fixed_pressure", **mixture(sp_n2=0.5, sp_o2=0.5))})
    other_members = request(pressure=finite(1.0), collider=collider("sp_n2", "sp_ar", fractions=(0.5, 0.5)))
    assert "collider_mismatch" in codes(verdict(half, other_members))  # same fractions, different components
    # A mixture-evaluated record does not answer a single-collider question, nor the reverse.
    assert "collider_mismatch" in codes(verdict(record, request(pressure=finite(1.0), collider=collider("sp_n2"))))
    assert "collider_mismatch" in codes(verdict(fixed_record(), exact))


def test_composition_effective_basis_needs_an_already_evaluated_mixture_record():
    req = request(
        coefficient_basis=Basis.composition_effective_coefficient,
        collider=collider("sp_n2", "sp_o2", fractions=(0.79, 0.21)),
    )
    effective = norm(applicability=applicability(coefficient_basis="composition_effective_coefficient", **mixture(sp_n2=0.79, sp_o2=0.21)))
    assert verdict(effective, req).applicability is A.applicable
    kernel_like = norm(applicability=applicability(coefficient_basis="composition_effective_coefficient", collider_kind="composition_dependent"))
    assert "collider_mismatch" in codes(verdict(kernel_like, req))
    not_dependent = norm(applicability=applicability(coefficient_basis="composition_effective_coefficient", collider_kind="not_dependent"))
    assert "collider_mismatch" in codes(verdict(not_dependent, req))


def test_a_composition_dependent_record_needs_an_efficiency_for_every_requested_collider():
    block = applicability(pressure_dependence="fixed_pressure", collider_kind="composition_dependent")
    base = {**fixed_record().__dict__, "applicability": block}
    req = request(pressure=finite(1.0), collider=collider("sp_n2", "sp_he", fractions=(0.5, 0.5)))
    assert verdict(norm(**{**base, "efficiencies": {"sp_n2": 1.0, "sp_he": 0.8}}), req).applicability is A.applicable
    missing = verdict(norm(**{**base, "efficiencies": {"sp_n2": 1.0}}), req)
    assert missing.applicability is A.unresolved and "third_body_efficiency_missing_for_collider" in codes(missing)
    default = applicability(pressure_dependence="fixed_pressure", collider_kind="composition_dependent", default_third_body_efficiency=1.0)
    assert verdict(norm(**{**base, "applicability": default, "efficiencies": {}}), req).applicability is A.applicable


def test_a_collider_is_not_required_when_it_is_not_material():
    # Independent / HPL elementary request: a record's collider statements are not consulted.
    assert verdict(norm(applicability=applicability(**specified("sp_he")))).applicability is A.applicable


# -- precedence and evidence ----------------------------------------------------------------------


def test_incompatible_outranks_unsupported_outranks_unresolved():
    a = verdict(norm(direction="net", applicability=applicability(phase="liquid", coefficient_basis=None)))
    assert a.applicability is A.incompatible
    b = verdict(norm(direction="net", applicability=applicability(coefficient_basis=None)))
    assert b.applicability is A.unsupported
    assert {r.applicability for r in b.reasons} == {A.unsupported, A.unresolved}


def test_an_experimental_rate_needs_no_computational_chain_and_a_computed_hard_fail_blocks():
    from app.services.trust.models import EvidenceBadge, EvidenceEvaluation, HardFailReason

    experimental = assess_candidate(norm(scientific_origin="experimental"), request=request(), evidence=None)
    assert experimental.physically_eligible and "evidence_rubric_not_applicable:experimental" in experimental.advisory
    failed = EvidenceEvaluation(
        record_type="kinetics", record_id=1, rubric="computed_kinetics_v1", rubric_version="1",
        label=EvidenceBadge.hard_failed, checks={}, passed_count=0, possible_count=1, evidence_completeness=0.0,
        is_certified=False, hard_fail_reason=HardFailReason.source_calculation_hard_failed_for_required_role,
        check_results=(),
    )
    a = assess_candidate(norm(), request=request(), evidence=failed)
    assert a.applicability is A.applicable and not a.physically_eligible
    assert a.blocking == ("evidence_hard_failed:source_calculation_hard_failed_for_required_role",)


# -- a pressure surface is its own pressure claim; a plain fit that says nothing is not --------------


def test_a_pressure_surface_needs_no_declared_pressure_dependence_but_a_plain_fit_does():
    n2 = collider("sp_n2")
    surface = plog_record()
    undeclared = norm(**{**surface.__dict__, "applicability": applicability(pressure_dependence=None, collider_kind="not_dependent")})
    assert verdict(undeclared, request(pressure=finite(1.0), collider=n2)).applicability is A.applicable
    # ... and being a surface it is no pressure-independent coefficient.
    assert "pressure_dependence_mismatch" in codes(verdict(undeclared))
    plain = norm(applicability=applicability(pressure_dependence=None, collider_kind="not_dependent"))
    unresolved = verdict(plain, request(pressure=finite(1.0), collider=n2))
    assert unresolved.applicability is A.unresolved and "pressure_dependence_not_declared" in codes(unresolved)
