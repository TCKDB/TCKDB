"""One directed channel: which determinations answer the requested coefficient, and why each other cannot.

Every test asserts the exact verdict and the exact reason code, and each negative case has a positive twin
(``test_a_complete_declared_determination_is_applicable``) that differs from it by one fact, so a check that
silently stopped firing cannot pass as "applicable".
"""

from __future__ import annotations

import pytest
from tckdb_schemas.network_declarations import NetworkObservable, NetworkRegimeKind

from app.db.models.common import RecordReviewStatus
from app.services.network_selection import NetworkRequest, assess_network
from app.services.selection_kernel import Applicability as A
from tests.services.network_selection._requests import channel_request
from tests.services.network_selection._world import add_solve, fit_spec, set_review, target, validity


def assess(session, request: NetworkRequest):
    return assess_network(session, request=request, require_snapshot=False)


def only(result):
    (assessment,) = result.determination_assessments
    return assessment


def codes(assessment) -> set[str]:
    return {r.code for r in assessment.reasons}


def has(assessment, code: str) -> bool:
    """A reason code, with or without the ``:argument`` some codes carry."""
    return any(c == code or c.startswith(code + ":") for c in codes(assessment))


def test_a_complete_declared_determination_is_applicable(db_session, world):
    solve = add_solve(db_session, world, fits=[fit_spec("assoc")])
    a = only(assess(db_session, channel_request(world)))
    assert (a.applicability, a.reasons, a.blocking) == (A.applicable, (), ())
    assert a.physically_eligible
    assert a.eligible_fit_refs == (solve._fits[0].public_ref,)
    assert a.solve_ref == solve.public_ref and a.channel_key == "assoc"


def test_alternate_fits_of_one_determination_are_one_assessment_and_two_determinations_are_two(db_session, world):
    add_solve(
        db_session,
        world,
        fits=[
            fit_spec("assoc", rep="plog1"),
            fit_spec("assoc", rep="cheb1", model="chebyshev", pmin=0.01, pmax=100.0),
        ],
    )
    add_solve(db_session, world, fits=[fit_spec("assoc", det="d_other")])
    result = assess(db_session, channel_request(world))
    first, second = result.determination_assessments
    assert len(first.eligible_fit_refs) == 2  # both fits of one determination, one candidate
    assert len(second.eligible_fit_refs) == 1
    assert first.determination_ref != second.determination_ref


def test_parallel_channel_keys_with_the_same_endpoints_are_distinct_targets(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("elim"), fit_spec("elim_alt")])
    well, exit_ = world.hashes["well"], world.hashes["exit"]
    result = assess(
        db_session,
        channel_request(world, channel_key="elim", source_composition_hash=well, sink_composition_hash=exit_),
    )
    a = only(result)  # only the requested key's determination is in the population
    assert a.channel_key == "elim" and a.applicability is A.applicable
    other = only(assess(db_session, channel_request(world, channel_key="elim_alt")))
    assert other.channel_key == "elim_alt" and other.determination_ref != a.determination_ref


def test_a_reversed_direction_is_not_the_requested_channel(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")])
    forward = channel_request(
        world, source_composition_hash=world.hashes["ent"], sink_composition_hash=world.hashes["well"]
    )
    assert only(assess(db_session, forward)).applicability is A.applicable
    reversed_ = channel_request(
        world, source_composition_hash=world.hashes["well"], sink_composition_hash=world.hashes["ent"]
    )
    a = only(assess(db_session, reversed_))
    assert a.applicability is A.incompatible and "directed_endpoints_mismatch" in codes(a)


def test_a_channel_with_no_determination_has_no_candidate_and_its_fits_are_disclosed_not_grouped(db_session, world):
    solve = add_solve(db_session, world, fits=[fit_spec("assoc", det=None)])
    result = assess(db_session, channel_request(world))
    assert result.determination_assessments == ()
    assert result.ungrouped_fit_refs == (solve._fits[0].public_ref,)
    assert solve._fits[0].determination_id is None  # nothing invented a determination for it


@pytest.mark.parametrize(
    "target_changes, code, verdict",
    [
        ({"partition": {"retained": ["a" * 64, "b" * 64], "eliminated": ["c" * 64], "lumps": []}}, "partition_differs", A.incompatible),
        ({"partition": None}, "partition_not_declared", A.unresolved),
        ({"boundaries": [{"state_key": "c" * 64, "kind": "reversible"}]}, "boundary_differs", A.incompatible),
        ({"regime": None}, "regime_not_declared", A.unresolved),
        ({"regime": {"kind": "initial_population_restricted", "initial_state_keys": ["a" * 64]}}, "regime_differs", A.incompatible),
        ({"validity": None}, "physical_validity_not_declared", A.unresolved),
        ({"validity": validity(temperature_max_k=1000.0)}, "request_outside_declared_validity", A.incompatible),
        ({"validity": validity(pressure_max_bar=2.0)}, "request_outside_declared_validity", A.incompatible),
    ],
)
def test_the_declared_target_must_establish_the_request(db_session, world, target_changes, code, verdict):
    add_solve(db_session, world, fits=[fit_spec("assoc")], solve_target=target(world, **target_changes))
    request = channel_request(world, boundaries=((world.hashes["exit"], "absorbing"),))
    a = only(assess(db_session, request))
    assert a.applicability is verdict and has(a, code)
    assert not a.physically_eligible and a.eligible_fit_refs == ()


def test_a_solve_with_no_target_declaration_is_unresolved_and_never_assumed_standard(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")], solve_target=None)
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.unresolved and "target_not_declared" in codes(a)


def test_an_unreadable_target_declaration_is_unresolved_with_its_own_reason(db_session, world):
    solve = add_solve(db_session, world, fits=[fit_spec("assoc")], review=None)
    solve.target_declaration = {"version": 1, "claim_origin": "nobody_we_know"}
    db_session.flush()
    set_review(db_session, world, solve, RecordReviewStatus.approved)
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.unresolved and "target_declaration_unreadable" in codes(a)


def test_a_declared_regime_restriction_must_match_the_requested_initial_population(db_session, world):
    restricted = {"kind": "initial_population_restricted", "initial_state_keys": [world.hashes["ent"]]}
    add_solve(db_session, world, fits=[fit_spec("assoc")], solve_target=target(world, regime=restricted))
    ok = channel_request(
        world,
        regime_kind=NetworkRegimeKind.initial_population_restricted,
        initial_state_hashes=(world.hashes["ent"],),
    )
    assert only(assess(db_session, ok)).applicability is A.applicable
    other = channel_request(
        world,
        regime_kind=NetworkRegimeKind.initial_population_restricted,
        initial_state_hashes=(world.hashes["well"],),
    )
    a = only(assess(db_session, other))
    assert a.applicability is A.incompatible and "initial_population_differs" in codes(a)


def test_a_different_observable_or_coefficient_basis_is_a_different_question(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")])
    loss = channel_request(world, observable=NetworkObservable.total_loss_coefficient)
    a = only(assess(db_session, loss))
    assert a.applicability is A.incompatible and "observable_mismatch" in codes(a)
    effective = channel_request(world, coefficient_basis="composition_effective")
    assert "coefficient_basis_mismatch" in codes(only(assess(db_session, effective)))
    degeneracy = channel_request(world, degeneracy_applied=False)
    assert "degeneracy_convention_mismatch" in codes(only(assess(db_session, degeneracy)))


def test_a_declared_order_that_contradicts_the_channels_source_state_is_incompatible(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc", observable={"reaction_order": 1})])
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.incompatible and "order_contradicts_channel_source" in codes(a)


def test_rate_units_whose_order_contradicts_the_channel_are_incompatible(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc", units="per_s", plog=[(0.1, 1.0, 0.0, 1.0), (10.0, 2.0, 0.0, 1.0)])])
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.incompatible and "rate_units_contradict_order" in codes(a)


def test_missing_rate_units_are_unresolved(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc", units=None)])
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.unresolved and "rate_units_not_stated" in codes(a)


# -- physical coverage ------------------------------------------------------------------------


def test_the_requested_window_must_lie_inside_solve_scope_fit_support_and_declared_validity(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc", tmax=1000.0)])  # fit support ends below the request
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.incompatible and "request_outside_fit_support" in codes(a)
    add_solve(db_session, world, fits=[fit_spec("assoc", det="d2")], scope={"tmax_k": 1000.0})  # solve scope too short
    second = assess(db_session, channel_request(world)).determination_assessments[1]
    assert "request_outside_solve_scope" in codes(second)


def test_a_summary_envelope_across_solves_certifies_no_solve(db_session, world):
    """Low-temperature and high-temperature solves together span the request; neither covers it."""
    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc", tmin=300.0, tmax=900.0)],
        solve_target=target(world, validity=validity(temperature_max_k=900.0)),
        scope={"tmax_k": 900.0},
    )
    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc", tmin=1000.0, tmax=2000.0, det="d_hi")],
        solve_target=target(world, validity=validity(temperature_min_k=1000.0)),
        scope={"tmin_k": 1000.0},
    )
    result = assess(db_session, channel_request(world, temperature_min_k=800.0, temperature_max_k=1100.0))
    assert [a.applicability for a in result.determination_assessments] == [A.incompatible, A.incompatible]
    assert not any(a.physically_eligible for a in result.determination_assessments)  # no stitching


def test_plog_pressure_anchors_bound_interpolation_and_are_never_extrapolated(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")])
    inside = assess(db_session, channel_request(world, pressure_min_bar=0.1, pressure_max_bar=10.0))
    assert only(inside).applicability is A.applicable  # exactly the anchors is covered
    above = assess(db_session, channel_request(world, pressure_min_bar=1.0, pressure_max_bar=20.0))
    a = only(above)
    assert a.applicability is A.incompatible and "request_outside_plog_anchors" in codes(a)


def test_a_plog_without_temperature_bounds_is_unresolved_without_declared_validity_even_though_it_evaluates(db_session, world):
    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc", tmin=None, tmax=None)],
        solve_target=target(world, validity=None),
    )
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.unresolved
    assert {"temperature_validity_not_declared", "physical_validity_not_declared"} <= codes(a)
    # The twin: a declared validity supplies the temperature statement.
    add_solve(db_session, world, fits=[fit_spec("assoc", tmin=None, tmax=None, det="d2")])
    assert assess(db_session, channel_request(world)).determination_assessments[1].applicability is A.applicable


def test_a_chebyshev_fit_needs_its_mapping_domain_and_intact_coefficients(db_session, world):
    kwargs = {"model": "chebyshev", "pmin": 0.01, "pmax": 100.0}
    add_solve(db_session, world, fits=[fit_spec("assoc", **kwargs)])
    assert only(assess(db_session, channel_request(world))).applicability is A.applicable
    add_solve(db_session, world, fits=[fit_spec("assoc", det="d2", pmin=None, **{k: v for k, v in kwargs.items() if k != "pmin"})])
    assert "chebyshev_mapping_domain_missing" in codes(assess(db_session, channel_request(world)).determination_assessments[1])
    add_solve(db_session, world, fits=[fit_spec("assoc", det="d3", coeffs=[[1.0, 0.5]], **kwargs)])
    broken = assess(db_session, channel_request(world)).determination_assessments[2]
    assert broken.applicability is A.incompatible and not broken.physically_eligible
    assert "representation_incomplete" in codes(broken) and broken.blocking


def test_chebyshev_axis_units_and_log_convention_are_never_defaulted(db_session, world):
    kwargs = {"model": "chebyshev", "pmin": 0.01, "pmax": 100.0}
    add_solve(db_session, world, fits=[fit_spec("assoc", stores_log10=None, **kwargs)])
    assert "stores_log10_k_not_stated" in codes(only(assess(db_session, channel_request(world))))
    add_solve(db_session, world, fits=[fit_spec("assoc", det="d2", pressure_units="atm", **kwargs)])
    second = assess(db_session, channel_request(world)).determination_assessments[1]
    assert second.applicability is A.unsupported and "axis_units_not_bar_kelvin" in codes(second)


def test_tabulated_fits_support_exact_points_only(db_session, world):
    points = [(1000.0, 1.0, 1.0e-12), (1200.0, 1.0, 2.0e-12)]
    add_solve(db_session, world, fits=[fit_spec("assoc", model="tabulated", points=points)])
    point = channel_request(
        world, temperature_min_k=1000.0, temperature_max_k=1000.0, pressure_min_bar=1.0, pressure_max_bar=1.0
    )
    assert only(assess(db_session, point)).applicability is A.applicable
    interval = channel_request(world, temperature_min_k=1000.0, temperature_max_k=1200.0, pressure_min_bar=1.0, pressure_max_bar=1.0)
    a = only(assess(db_session, interval))
    assert a.applicability is A.unsupported and "tabulated_interval_unsupported" in codes(a)  # extrema are not coverage
    missing = channel_request(world, temperature_min_k=1100.0, temperature_max_k=1100.0, pressure_min_bar=1.0, pressure_max_bar=1.0)
    b = only(assess(db_session, missing))
    assert b.applicability is A.incompatible and "tabulated_point_absent" in codes(b)  # absent is not zero


def test_additive_and_overlapping_pieces_are_never_candidates_and_never_summed(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc", role="additive_component", rep="part1")])
    add_solve(db_session, world, fits=[fit_spec("assoc", det="d2", role="overlapping_contribution")])
    first, second = assess(db_session, channel_request(world)).determination_assessments
    for a, code in ((first, "additive_component_not_a_complete_determination"), (second, "overlapping_contribution_not_a_complete_determination")):
        assert a.applicability is A.unsupported and a.eligible_fit_refs == ()
        assert "no_complete_representation" in codes(a)
        assert any(code in item for item in a.advisory)


def test_a_complete_fit_survives_an_additive_sibling_of_its_determination(db_session, world):
    solve = add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc", rep="whole"), fit_spec("assoc", rep="part", role="additive_component")],
    )
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.applicable and a.eligible_fit_refs == (solve._fits[0].public_ref,)
    assert any("component_not_selected" in item for item in a.advisory)


def test_negative_fitted_terms_and_activation_energy_are_not_defects(db_session, world):
    rows = [(0.1, -1.0e13, -0.5, -12.0), (10.0, 2.0e13, 0.1, 42.0)]
    add_solve(db_session, world, fits=[fit_spec("assoc", plog=rows)])
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.applicable and a.blocking == ()


def test_non_finite_plog_coefficients_block_the_determination(db_session, world):
    rows = [(0.1, float("inf"), 0.0, 40.0), (10.0, 2.0e13, 0.1, 42.0)]
    add_solve(db_session, world, fits=[fit_spec("assoc", plog=rows)])
    a = only(assess(db_session, channel_request(world)))
    assert not a.physically_eligible and a.blocking and "non_finite_coefficient" in codes(a)


# -- bath --------------------------------------------------------------------------------------


def test_the_solves_own_bath_rows_decide_the_collider(db_session, world):
    from app.services.network_selection import BathRequest

    add_solve(db_session, world, fits=[fit_spec("assoc")])
    other = channel_request(world, bath=BathRequest((world.he.public_ref,)))
    a = only(assess(db_session, other))
    assert a.applicability is A.incompatible and "bath_species_differ" in codes(a)


def test_a_fixed_mixture_matches_by_explicit_composition_and_is_never_renormalised(db_session, world):
    from app.services.network_selection import BathRequest

    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc")],
        bath=[(world.ar, 0.7), (world.he, 0.3)],
        solve_target=target(world, bath_scope="fixed_mixture"),
    )
    same = channel_request(world, bath=BathRequest((world.ar.public_ref, world.he.public_ref), (0.7, 0.3)))
    assert only(assess(db_session, same)).applicability is A.applicable
    shifted = channel_request(world, bath=BathRequest((world.ar.public_ref, world.he.public_ref), (0.6, 0.4)))
    assert "bath_composition_differs" in codes(only(assess(db_session, shifted)))
    with pytest.raises(ValueError, match="never renormalised"):
        BathRequest((world.ar.public_ref, world.he.public_ref), (0.7, 0.2))


def test_a_composition_dependent_bath_is_unsupported_and_no_mixture_is_summed_from_pure_colliders(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")], solve_target=target(world, bath_scope="composition_dependent"))
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.unsupported and "composition_dependent_bath_unsupported" in codes(a)


def test_an_unstated_bath_is_unresolved(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")], bath=[])
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.unresolved and "bath_not_stated" in codes(a)


# -- output catalog, protocol, evidence, solve kinds --------------------------------------------


@pytest.mark.parametrize(
    "entry, code",
    [
        ({"availability": "declared_zero", "zero_basis": "source_statement"}, "zero_claim_contradicts_determination"),
        ({"availability": "unavailable"}, "output_declared_unavailable"),
    ],
)
def test_a_channel_declared_unavailable_or_zero_cannot_also_be_a_determination(db_session, world, entry, code):
    outputs = [{"channel_key": "assoc", "required": True, **entry}]
    add_solve(db_session, world, fits=[fit_spec("assoc")], solve_target=target(world, outputs=outputs))
    a = only(assess(db_session, channel_request(world)))
    assert a.applicability is A.incompatible and code in codes(a)
    # The twin: the same catalog entry for another channel does not touch this one.
    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc", det="d2")],
        solve_target=target(world, outputs=[{**outputs[0], "channel_key": "elim"}]),
    )
    assert assess(db_session, channel_request(world)).determination_assessments[1].applicability is A.applicable


def test_an_applicable_solve_with_unknown_protocol_stays_eligible_and_says_so(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")])
    a = only(assess(db_session, channel_request(world)))
    assert a.physically_eligible and "protocol_not_declared" in a.advisory  # unknown evidence does not exclude


def test_evidence_is_reported_as_declared_or_unavailable_and_never_as_verified(db_session, world):
    validation = {
        "version": 1,
        "entries": [{"kind": "convergence", "metric": "max_relative_error", "value": 0.01, "domain": validity()}],
    }
    add_solve(db_session, world, fits=[fit_spec("assoc")], validation=validation)
    a = only(assess(db_session, channel_request(world)))
    assert a.evidence["convergence"] == "declared"
    assert a.evidence["model_fidelity"] == "unavailable"
    assert set(a.evidence.values()) <= {"declared", "unavailable"}  # verified needs a verification not made here


def test_a_reported_solve_needs_no_computed_inputs_to_be_assessed(db_session, world):
    """No state energies, barriers or transition-state evidence are fabricated or demanded of a literature solve."""
    from app.db.models.common import NetworkSolveKind

    solve = add_solve(db_session, world, fits=[fit_spec("assoc")], kind=NetworkSolveKind.reported)
    a = only(assess(db_session, channel_request(world)))
    assert a.physically_eligible and a.solve_ref == solve.public_ref
    assert solve.state_energies == [] and solve.channel_barriers == [] and solve.source_calculations == []
