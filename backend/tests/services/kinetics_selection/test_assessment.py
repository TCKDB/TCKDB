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


def test_the_progress_convention_matters_for_a_squared_species_and_only_the_canonical_one_is_assessed():
    # 2 H -> H2: one reactant counted twice. The same number is a different coefficient under another convention.
    progress = norm(reactant_stoichiometries=(2,), applicability=applicability(rate_progress_convention="reaction_progress"))
    assert verdict(progress).applicability is A.applicable
    loss = verdict(norm(reactant_stoichiometries=(2,), applicability=applicability(rate_progress_convention="reactant_loss")))
    assert loss.applicability is A.unsupported
    assert "rate_progress_convention_unsupported:reactant_loss" in codes(loss) and not loss.physically_eligible
    # Nothing is converted, so the two conventions can never both answer one request.
    assert A.applicable not in {verdict(norm(reactant_stoichiometries=(2,), applicability=applicability(rate_progress_convention=c))).applicability for c in ("reactant_loss",)}
    # With every species counted once the convention does not change the number, so it is not needed or judged.
    assert verdict(norm(reactant_stoichiometries=(1, 1))).applicability is A.applicable
    assert verdict(norm(reactant_stoichiometries=(1, 1), applicability=applicability(rate_progress_convention="reactant_loss"))).applicability is A.applicable


def test_a_reverse_coefficient_reads_the_products_for_its_convention_not_the_reactants():
    reverse = request(direction=KineticsDirection.reverse)
    # H + H -> H2 read in reverse: the coefficient is of H2 -> 2 H, normalised on the products (H, H count 2).
    base = {"direction": "reverse", "determination": determination(direction="reverse"), "a_units": "per_s",
            "applicability": applicability(reaction_order=1)}
    squared_products = norm(reactant_stoichiometries=(1,), product_stoichiometries=(2,), **base)
    undeclared = verdict(squared_products, reverse)
    assert undeclared.applicability is A.unresolved and "rate_progress_convention_not_declared" in codes(undeclared)
    declared = norm(reactant_stoichiometries=(1,), product_stoichiometries=(2,), **{**base, "applicability": applicability(reaction_order=1, rate_progress_convention="reaction_progress")})
    assert verdict(declared, reverse).applicability is A.applicable
    # Squared reactants are not what a reverse coefficient is normalised on.
    only_reactants = norm(reactant_stoichiometries=(2,), product_stoichiometries=(1,), **base)
    assert verdict(only_reactants, reverse).applicability is A.applicable
    # And the forward direction of that very reaction reads the reactants.
    assert "rate_progress_convention_not_declared" in codes(verdict(norm(reactant_stoichiometries=(2,), product_stoichiometries=(1,))))


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
    multi = norm(model_kind="multi_arrhenius", a=None, a_units=None, arrhenius_terms=2,
                 arrhenius_units=("cm3_molecule_s", "cm3_molecule_s"))
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
    record = norm(model_kind="chebyshev", a=None, a_units="cm3_molecule_s", chebyshev=ch, applicability=applicability(pressure_dependence="pressure_dependent"))
    assert "temperature_range_not_covered" in codes(verdict(record, request(pressure=finite(1.0), collider=collider("sp_n2"))))


# -- pressure ------------------------------------------------------------------------------------


def hpl_record(**changes):
    return norm(pressure_context="high_p_limit", applicability=applicability(pressure_dependence="high_pressure_limit"), **changes)


def test_finite_pressure_versus_the_high_pressure_limit_and_independence():
    hpl_request = request(pressure=PressureRequest(PressureKind.high_pressure_limit))
    assert verdict(hpl_record(), hpl_request).applicability is A.applicable
    assert "pressure_dependence_mismatch" in codes(verdict(hpl_record()))  # independent request, HPL record
    # An established pressure independence answers a high-pressure-limit request too; the reverse is not true.
    assert verdict(norm(), hpl_request).applicability is A.applicable
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


def plog_record(pressures=(0.1, 1.0, 10.0), units="cm3_molecule_s", **block):
    return norm(
        model_kind="plog", a=None, a_units=None, plog_pressures_bar=tuple(pressures),
        plog_units=tuple(units for _ in pressures),
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
        model_kind="chebyshev", a=None, a_units="cm3_molecule_s", chebyshev=ch,
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


# -- units, order and content of every representation, not of a parent row ---------------------------


def multi(units, **kw):
    return norm(model_kind="multi_arrhenius", a=None, a_units=None, arrhenius_terms=len(units), arrhenius_units=tuple(units), **kw)


def troe(**falloff):
    content = {"low_a_units": "cm6_molecule2_s", "low_a": 1.0e-30, "troe_alpha": 0.6, "troe_t3": 100.0, "troe_t1": 2000.0,
               "troe_t2": None, "sri_a": None, "sri_b": None, "sri_c": None, "sri_d": None, "sri_e": None}
    content.update(falloff)
    return norm(
        model_kind="troe", has_falloff=True, falloff=content, pressure_context="pressure_dependent",
        applicability=applicability(pressure_dependence="pressure_dependent", collider_kind="not_dependent",
                                    pressure_domain_min_bar=0.01, pressure_domain_max_bar=100.0),
    )


def at_one_bar():
    return request(pressure=finite(1.0), collider=collider("sp_n2"))


@pytest.mark.parametrize(
    "record,request_changes,expected,code",
    [
        (multi(["cm3_molecule_s", "cm3_molecule_s"]), {}, A.applicable, None),
        (multi(["cm3_molecule_s", None]), {}, A.unresolved, "a_units_not_recorded"),
        (multi([None, None]), {}, A.unresolved, "a_units_not_recorded"),
        (multi(["cm3_molecule_s", "per_s"]), {}, A.incompatible, "units_orders_inconsistent"),
        (multi(["cm3_molecule_s", "cm6_molecule2_s"]), {}, A.incompatible, "units_orders_inconsistent"),
        (multi(["per_s", "per_s"]), {}, A.incompatible, "reaction_order_units_mismatch"),  # declared order is 2
    ],
    ids=["multi_ok", "multi_one_term_without_units", "multi_no_term_units", "multi_mixed_orders", "multi_mixed_orders_2_3", "multi_wrong_order"],
)
def test_every_term_of_a_multi_arrhenius_fit_carries_its_units(record, request_changes, expected, code):
    a = verdict(record, request(**request_changes))
    assert a.applicability is expected
    if code:
        assert code in codes(a)


@pytest.mark.parametrize(
    "units,expected,code",
    [
        (("cm3_molecule_s", "cm3_molecule_s", "cm3_molecule_s"), A.applicable, None),
        (("cm3_molecule_s", None, "cm3_molecule_s"), A.unresolved, "a_units_not_recorded"),
        (("cm3_molecule_s", "per_s", "cm3_molecule_s"), A.incompatible, "units_orders_inconsistent"),
        (("per_s", "per_s", "per_s"), A.incompatible, "reaction_order_units_mismatch"),
    ],
    ids=["plog_ok", "plog_entry_without_units", "plog_mixed_orders", "plog_wrong_order"],
)
def test_every_entry_of_a_plog_fit_carries_its_units(units, expected, code):
    record = norm(**{**plog_record().__dict__, "plog_units": units})
    a = verdict(record, at_one_bar())
    assert a.applicability is expected
    if code:
        assert code in codes(a)


def test_a_plog_fit_with_a_negative_a_term_and_no_declared_domain_covers_its_anchor_range():
    # A negative fitted A is not a defect, and with no declared validity domain the table's own anchors bound it.
    assert verdict(plog_record(), request(pressure=finite(0.1, 10.0), collider=collider("sp_n2"))).applicability is A.applicable
    assert "pressure_range_not_covered" in codes(verdict(plog_record(), request(pressure=finite(0.05, 10.0), collider=collider("sp_n2"))))


def test_a_chebyshev_surface_needs_its_rate_units_and_their_order():
    assert verdict(chebyshev_record(), at_one_bar()).applicability is A.applicable
    missing = verdict(norm(**{**chebyshev_record().__dict__, "a_units": None}), at_one_bar())
    assert missing.applicability is A.unresolved and "a_units_not_recorded" in codes(missing)
    wrong = verdict(norm(**{**chebyshev_record().__dict__, "a_units": "per_s"}), at_one_bar())
    assert wrong.applicability is A.incompatible and "reaction_order_units_mismatch" in codes(wrong)


def test_a_falloff_fit_needs_its_low_pressure_units_one_order_above_its_high_pressure_line():
    # The Troe fixture is a +M reaction written A + B (+M): k_inf second order, k_0 third order.
    assert verdict(troe(), at_one_bar()).applicability is A.applicable
    missing = verdict(troe(low_a_units=None), at_one_bar())
    assert missing.applicability is A.unresolved and "low_pressure_units_not_recorded" in codes(missing)
    for low in ("cm3_molecule_s", "per_s"):
        wrong = verdict(troe(low_a_units=low), at_one_bar())
        assert wrong.applicability is A.incompatible and "falloff_low_pressure_order_inconsistent" in codes(wrong)


@pytest.mark.parametrize(
    "kind,missing",
    [("troe", "troe_alpha"), ("troe", "troe_t3"), ("troe", "troe_t1"), ("troe", "low_a"),
     ("sri", "sri_a"), ("sri", "sri_b"), ("sri", "sri_c"), ("lindemann", "low_a")],
)
def test_a_falloff_fit_is_incomplete_without_the_parameters_its_model_needs(kind, missing):
    base = troe().__dict__
    content = {"low_a_units": "cm6_molecule2_s", "low_a": 1.0e-30, "troe_alpha": 0.6, "troe_t3": 100.0, "troe_t1": 2000.0,
               "troe_t2": None, "sri_a": 1.0, "sri_b": 2.0, "sri_c": 3.0, "sri_d": None, "sri_e": None}
    assert verdict(norm(**{**base, "model_kind": kind, "falloff": content}), at_one_bar()).applicability is A.applicable
    content[missing] = None
    incomplete = verdict(norm(**{**base, "model_kind": kind, "falloff": content}), at_one_bar())
    assert incomplete.applicability is A.incompatible and "representation_content_missing" in codes(incomplete)


def test_optional_falloff_parameters_are_not_required():
    for name in ("troe_t2", "sri_d", "sri_e"):
        assert verdict(troe(**{name: None}), at_one_bar()).applicability is A.applicable


# -- a pressure-independent rate answers every pressure request and needs no bath gas ----------------


def test_an_established_pressure_independence_answers_a_finite_pressure_with_no_collider_statement():
    n2_at_one_bar = at_one_bar()
    independent = norm(applicability=applicability(pressure_dependence="independent", collider_kind=None))
    a = verdict(independent, n2_at_one_bar)
    assert a.applicability is A.applicable and not a.reasons
    # Whatever it says about colliders short of naming a different one does not matter.
    for kind in ("not_dependent", "composition_dependent"):
        block = applicability(pressure_dependence="independent", collider_kind=kind)
        assert verdict(norm(applicability=block), n2_at_one_bar).applicability is A.applicable, kind


def test_a_pressure_independent_rate_that_names_another_collider_is_still_for_that_collider():
    he = norm(applicability=applicability(pressure_dependence="independent", collider_kind="specified_collider",
                                          colliders=[{"species_ref": "sp_he", "mole_fraction": None}]))
    assert "collider_mismatch" in codes(verdict(he, at_one_bar()))
    n2 = norm(applicability=applicability(pressure_dependence="independent", collider_kind="specified_collider",
                                          colliders=[{"species_ref": "sp_n2", "mole_fraction": None}]))
    assert verdict(n2, at_one_bar()).applicability is A.applicable


def test_only_an_unknown_pressure_dependence_stays_unresolved_for_a_finite_request():
    unknown = verdict(norm(applicability=applicability(pressure_dependence=None)), at_one_bar())
    assert unknown.applicability is A.unresolved and "pressure_dependence_not_declared" in codes(unknown)
    # And an established independence does not make a composition-effective coefficient answerable.
    effective = request(coefficient_basis=Basis.composition_effective_coefficient, pressure=finite(1.0),
                        collider=collider("sp_n2", "sp_o2", fractions=(0.79, 0.21)))
    plain = norm(applicability=applicability(pressure_dependence="independent", coefficient_basis="composition_effective_coefficient"))
    assert verdict(plain, effective).applicability is A.unresolved
