"""The network preference engine over eligible nodes, on the shared kernel.

The rules used here are SYNTHETIC test fixtures (``tests/services/network_selection/_rules.py``): they build edges,
conflicts and cycles so the engine's logic is exercised, and they are not scientific benchmarks. The shipped
registry has no active rule, which the first tests assert.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tckdb_schemas.network_declarations import NetworkComparisonObjective as Objective

from app.db.models.common import RecordReviewStatus
from app.services.network_selection import NetworkRule, Scope, default_rules, select_network
from app.services.selection_kernel import Outcome
from tests.services.network_selection._requests import bundle_request, channel_request
from tests.services.network_selection._rules import (
    FitKindRule,
    ProtocolRule,
    protocol,
)
from tests.services.network_selection._world import add_solve, fit_spec, set_review, target

A_, B_, C_ = "chemically_significant_eigenvalues", "modified_strong_collision", "reservoir_state"


def select(session, request, rules=()):
    return select_network(session, request=request, rules=rules, require_snapshot=False)


def solve_with(db_session, world, reduction: str | None, **changes):
    options = {"fits": [fit_spec("assoc")]}
    if reduction is not None:
        options["protocol"] = protocol(reduction)
    options.update(changes)
    return add_solve(db_session, world, **options)


def ref(solve, key: str = "d_assoc") -> str:
    return solve._dets[key].public_ref


def test_the_shipped_registry_applies_no_rule_so_eligible_alternatives_stay_unranked(db_session, world):
    assert default_rules() == ()
    s, t = solve_with(db_session, world, A_), solve_with(db_session, world, B_)
    result = select_network(db_session, request=channel_request(world), require_snapshot=False)
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert result.decision.edges == () and result.decision.rules == ()
    assert [set(front) for front in result.decision.fronts] == [{ref(s), ref(t)}]
    assert "no registered rule compares" in result.decision.basis


def test_no_eligible_candidate_and_a_sole_candidate_are_their_own_outcomes(db_session, world):
    none = select(db_session, channel_request(world))
    assert none.outcome is Outcome.no_applicable_candidate and none.selected_ref is None
    only = solve_with(db_session, world, None)
    sole = select(db_session, channel_request(world))
    assert sole.outcome is Outcome.sole_eligible_candidate and sole.selected_ref == ref(only)
    assert sole.selection_basis == "sole_eligible_candidate"  # not a comparative accuracy statement
    assert "not a comparative or global-best claim" in sole.decision.basis


def test_an_active_rule_with_stated_and_compatible_sides_yields_a_scoped_preference(db_session, world):
    s, t = solve_with(db_session, world, A_), solve_with(db_session, world, B_)
    rule = ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_)
    result = select(db_session, channel_request(world), [rule])
    assert result.outcome is Outcome.policy_preferred and result.selected_ref == ref(s)
    assert result.selection_basis == "scientific_preference"
    (edge,) = result.decision.edges
    assert (edge["preferred"], edge["dispreferred"], edge["rule_id"]) == (ref(s), ref(t), "T-PREFER-A")
    assert edge["label"] == "expected-performance inference" and edge["objective"] == "physical_accuracy"
    assert result.decision.fronts == ((ref(s),), (ref(t),))


def test_an_applicable_candidate_with_unknown_protocol_prevents_a_unique_winner(db_session, world):
    s, t, u = solve_with(db_session, world, A_), solve_with(db_session, world, B_), solve_with(db_session, world, None)
    result = select(db_session, channel_request(world), [ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_)])
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert set(result.decision.fronts[0]) == {ref(s), ref(u)}  # u has no incoming edge: unknown is not a vote
    assert result.decision.fronts[1] == (ref(t),)


def test_a_rule_whose_pair_compatibility_is_unknown_or_false_makes_no_edge(db_session, world):
    solve_with(db_session, world, A_)
    solve_with(db_session, world, B_)
    rule = ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_, compat=("rotor_treatment",))
    result = select(db_session, channel_request(world), [rule])  # rotor treatment stated on neither side: unknown
    assert result.outcome is Outcome.incomparable_alternatives and result.decision.edges == ()
    (check,) = result.decision.pair_checks
    assert check["compatible"]["state"] == "unknown"
    other = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_, rotor_treatment="hindered_rotor"))
    third = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_, rotor_treatment="anharmonic"))
    again = select(db_session, channel_request(world), [rule])
    states = {(c["preferred"], c["dispreferred"]): c["compatible"]["state"] for c in again.decision.pair_checks}
    assert states[(ref(other), ref(third))] == "false"  # stated and different on the compared-away component
    assert all(e["preferred"] != ref(other) or e["dispreferred"] != ref(third) for e in again.decision.edges)


def test_a_rule_of_another_objective_is_not_applied_and_says_why(db_session, world):
    s, t = solve_with(db_session, world, A_), solve_with(db_session, world, B_)
    model_rule = ProtocolRule("T-MODEL", prefer=A_, yield_=B_, objective=Objective.model_fidelity)
    result = select(db_session, channel_request(world), [model_rule])
    assert result.outcome is Outcome.incomparable_alternatives
    (match,) = result.decision.rule_matches
    assert match["applied"] is False and match["why"] == "rule_objective_model_fidelity_not_requested"
    # The same rule under a request that states its objective (and pins its reference model) is applied.
    reference = ref(s)
    assert reference
    pinned = channel_request(
        world, objective=Objective.model_fidelity, reference_model_ref=world.solves[0].public_ref
    )
    applied = select(db_session, pinned, [model_rule])
    assert applied.outcome is Outcome.policy_preferred and applied.selected_ref == ref(s) and ref(t)


def test_inactive_and_revoked_rules_make_no_edge_and_appear_in_the_decision(db_session, world):
    solve_with(db_session, world, A_)
    solve_with(db_session, world, B_)
    rules = [
        ProtocolRule("T-INACTIVE", prefer=A_, yield_=B_, status="inactive"),
        ProtocolRule("T-REVOKED", prefer=A_, yield_=B_, status="revoked"),
    ]
    result = select(db_session, channel_request(world), rules)
    assert result.outcome is Outcome.incomparable_alternatives and result.decision.edges == ()
    assert {(m["rule_id"], m["why"]) for m in result.decision.rule_matches} == {
        ("T-INACTIVE", "rule_status_inactive"),
        ("T-REVOKED", "rule_status_revoked"),
    }


def test_edges_under_different_objective_keys_are_not_composed_into_a_path(db_session, world):
    s, t, u = (solve_with(db_session, world, r) for r in (A_, B_, C_))
    rules = [
        ProtocolRule("T-A-OVER-B", prefer=A_, yield_=B_, objective_key="convergence"),
        ProtocolRule("T-B-OVER-C", prefer=B_, yield_=C_, objective_key="reduction"),
    ]
    result = select(db_session, channel_request(world), rules)
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert result.decision.edges == () and len(result.decision.unused_edges) == 2
    assert "more than one objective" in result.decision.basis
    # One objective key composes: s over t over u is a chain, and s is preferred.
    same = [
        ProtocolRule("T-A-OVER-B", prefer=A_, yield_=B_, objective_key="convergence"),
        ProtocolRule("T-B-OVER-C", prefer=B_, yield_=C_, objective_key="convergence"),
    ]
    chained = select(db_session, channel_request(world), same)
    assert chained.outcome is Outcome.policy_preferred and chained.selected_ref == ref(s)
    assert chained.decision.fronts == ((ref(s),), (ref(t),), (ref(u),))


def test_opposing_rules_are_a_conflict_unless_one_declares_supersession(db_session, world):
    s, t = solve_with(db_session, world, A_), solve_with(db_session, world, B_)
    rules = [
        ProtocolRule("T-A-OVER-B", prefer=A_, yield_=B_),
        ProtocolRule("T-B-OVER-A", prefer=B_, yield_=A_),
    ]
    conflict = select(db_session, channel_request(world), rules)
    assert conflict.outcome is Outcome.policy_conflict and conflict.selected_ref is None
    assert conflict.selection_basis is None and conflict.decision.opposing_pairs
    resolved = select(
        db_session,
        channel_request(world),
        [rules[0], ProtocolRule("T-B-OVER-A", prefer=B_, yield_=A_, supersedes=("T-A-OVER-B",))],
    )
    assert resolved.outcome is Outcome.policy_preferred and resolved.selected_ref == ref(t)
    assert [e["overridden_by_rule_id"] for e in resolved.decision.overridden_edges] == ["T-B-OVER-A"]
    assert s is not None


def test_a_longer_cycle_is_a_conflict_with_no_first_selection_fallback(db_session, world):
    s, t, u = (solve_with(db_session, world, r) for r in (A_, B_, C_))
    rules = [
        ProtocolRule("T-A-B", prefer=A_, yield_=B_),
        ProtocolRule("T-B-C", prefer=B_, yield_=C_),
        ProtocolRule("T-C-A", prefer=C_, yield_=A_),
    ]
    result = select(db_session, channel_request(world, first_only=True), rules)
    assert result.outcome is Outcome.policy_conflict
    assert result.selected_ref is None and result.selection_basis is None
    assert result.decision.cycles == (tuple(sorted((ref(s), ref(t), ref(u)))),)


def test_an_administrative_first_is_labelled_and_never_changes_the_outcome(db_session, world):
    s = solve_with(db_session, world, A_)
    t = solve_with(db_session, world, B_)
    unranked = select(db_session, channel_request(world, first_only=True))
    assert unranked.outcome is Outcome.incomparable_alternatives  # the outcome is kept
    assert unranked.selection_basis == "administrative_first"
    assert unranked.decision.administrative_first["basis"].startswith("administrative order among unresolved")
    assert unranked.decision.administrative_first["node_ref"] in {ref(s), ref(t)}
    plain = select(db_session, channel_request(world))
    assert plain.selection_basis is None and plain.decision.administrative_first is not None


def test_administrative_policies_apply_no_rule(db_session, world):
    solve_with(db_session, world, A_)
    solve_with(db_session, world, B_)
    rule = ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_)
    result = select(db_session, channel_request(world, apply_rules=False), [rule])
    assert result.outcome is Outcome.incomparable_alternatives and result.decision.edges == ()
    assert result.decision.rules_applied is False and result.decision.rules == ()


def test_administrative_order_sorts_within_a_front_and_cannot_reverse_a_preference(db_session, world):
    old = solve_with(db_session, world, A_, review=RecordReviewStatus.not_reviewed)
    new = solve_with(db_session, world, B_, review=None)
    new.created_at = old.created_at + timedelta(days=30)  # before it is approved: an accepted row is immutable
    db_session.flush()
    set_review(db_session, world, new, RecordReviewStatus.approved)
    result = select(db_session, channel_request(world), [ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_)])
    # The newer, better-reviewed solve is administratively first, yet the accepted edge keeps the older one in front 1.
    assert result.decision.administrative_order[0] == ref(new)
    assert result.selected_ref == ref(old) and result.decision.fronts[0] == (ref(old),)


def test_each_solve_wins_a_different_window_and_no_cross_window_winner_is_formed(db_session, world):
    low = solve_with(
        db_session, world, A_,
        fits=[fit_spec("assoc", tmin=300.0, tmax=900.0)],
        solve_target=target(world, validity={"temperature_min_k": 300.0, "temperature_max_k": 900.0, "pressure_min_bar": 0.01, "pressure_max_bar": 100.0}),
        scope={"tmax_k": 900.0},
    )
    high = solve_with(
        db_session, world, B_,
        fits=[fit_spec("assoc", tmin=1000.0, tmax=2000.0)],
        solve_target=target(world, validity={"temperature_min_k": 1000.0, "temperature_max_k": 2000.0, "pressure_min_bar": 0.01, "pressure_max_bar": 100.0}),
        scope={"tmin_k": 1000.0},
    )
    rule = ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_)
    at_low = select(db_session, channel_request(world, temperature_min_k=400.0, temperature_max_k=800.0), [rule])
    assert at_low.outcome is Outcome.sole_eligible_candidate and at_low.selected_ref == ref(low)
    at_high = select(db_session, channel_request(world, temperature_min_k=1100.0, temperature_max_k=1900.0), [rule])
    assert at_high.outcome is Outcome.sole_eligible_candidate and at_high.selected_ref == ref(high)
    across = select(db_session, channel_request(world, temperature_min_k=400.0, temperature_max_k=1900.0), [rule])
    assert across.outcome is Outcome.no_applicable_candidate  # no averaging, no stitching


def test_bundle_nodes_need_every_output_to_be_preferred_so_opposite_channel_advantages_make_no_winner(db_session, world):
    outputs = [
        {"channel_key": "assoc", "availability": "supplied", "required": True},
        {"channel_key": "elim", "availability": "supplied", "required": True},
    ]
    options = {
        "fits": [fit_spec("assoc"), fit_spec("elim")],
        "solve_target": target(world, outputs=outputs),
        "product_sets": [{"key": "both", "members": [("d_assoc", []), ("d_elim", [])]}],
    }
    s = add_solve(db_session, world, protocol=protocol(A_), **options)
    t = add_solve(db_session, world, protocol=protocol(B_), **options)
    # SYNTHETIC: A is preferred for assoc, B for elim.
    split = ProtocolRule("T-SPLIT", prefer=A_, yield_=B_, channels={"assoc": (A_, B_), "elim": (B_, A_)})
    result = select(db_session, bundle_request(world), [split])
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert result.decision.edges == ()
    assert {n for front in result.decision.fronts for n in front} == {f"{s.public_ref}/both", f"{t.public_ref}/both"}
    # The twin: a rule that prefers A on every channel ranks the whole bundle.
    whole = ProtocolRule("T-WHOLE", prefer=A_, yield_=B_)
    ranked = select(db_session, bundle_request(world), [whole])
    assert ranked.outcome is Outcome.policy_preferred and ranked.selected_ref == f"{s.public_ref}/both"


def test_bundles_that_answer_different_outputs_are_not_compared_by_a_rule(db_session, world):
    base = {"solve_target": target(world, outputs=[
        {"channel_key": "assoc", "availability": "supplied", "required": True},
        {"channel_key": "elim", "availability": "declared_zero", "zero_basis": "source_statement", "required": True},
    ])}
    s = add_solve(db_session, world, protocol=protocol(A_), fits=[fit_spec("assoc")],
                  product_sets=[{"key": "p", "members": [("d_assoc", [])]}], **base)
    t = add_solve(db_session, world, protocol=protocol(B_), fits=[fit_spec("assoc"), fit_spec("elim")],
                  product_sets=[{"key": "p", "members": [("d_assoc", []), ("d_elim", [])]}],
                  solve_target=target(world, outputs=[
                      {"channel_key": "assoc", "availability": "supplied", "required": True},
                      {"channel_key": "elim", "availability": "supplied", "required": True}]))
    result = select(db_session, bundle_request(world), [ProtocolRule("T-WHOLE", prefer=A_, yield_=B_)])
    assert result.outcome is Outcome.incomparable_alternatives
    (check,) = result.decision.pair_checks
    assert check["compatible"] == {"state": "unknown", "reasons": ["members_answer_different_outputs"]}
    assert s is not None and t is not None


def test_alternate_fits_get_nested_fronts_that_rank_no_solve_and_are_never_independent(db_session, world):
    solve = add_solve(
        db_session,
        world,
        fits=[
            fit_spec("assoc", rep="plog1"),
            fit_spec("assoc", rep="cheb1", model="chebyshev", pmin=0.01, pmax=100.0),
        ],
        protocol=protocol(A_),
    )
    fit_rule = FitKindRule("T-FIT-PLOG", prefer="plog", yield_="chebyshev")
    physical = select(db_session, channel_request(world), [fit_rule])
    (row,) = physical.decision.representations
    assert len(row["fronts"]) == 1 and len(row["fronts"][0]) == 2  # administrative: the rule is not for this objective
    assert row["basis"].startswith("no representation rule applies") and row["independent_confirmation"] is False
    assert {m["why"] for m in physical.decision.rule_matches} == {"request_is_not_representation_fidelity"}
    fidelity = channel_request(world, objective=Objective.representation_fidelity, reference_outputs="solver output table")
    nested = select(db_session, fidelity, [fit_rule])
    (row,) = nested.decision.representations
    plog = next(f for f in solve._fits if f.model_kind.value == "plog").public_ref
    assert row["outcome"] == "policy_preferred" and row["chosen_fit_ref"] == plog
    assert row["shared_provenance"] == {"solve_ref": solve.public_ref} and row["independent_confirmation"] is False
    assert nested.outcome is Outcome.sole_eligible_candidate  # one solve; the fit preference ranked no solve


def test_a_registry_with_an_empty_objective_key_or_duplicate_ids_is_refused(db_session, world):
    solve_with(db_session, world, A_)
    with pytest.raises(ValueError, match="empty objective_key"):
        select(db_session, channel_request(world), [ProtocolRule("T-EMPTY", prefer=A_, yield_=B_, objective_key=" ")])
    twin = [ProtocolRule("T-DUP", prefer=A_, yield_=B_), ProtocolRule("T-DUP", prefer=B_, yield_=A_)]
    with pytest.raises(ValueError, match="distinct"):
        select(db_session, channel_request(world), twin)


def test_a_full_network_request_is_decided_like_any_bundle(db_session, world):
    channels = ("assoc", "diss", "elim", "elim_alt")
    outputs = [{"channel_key": c, "availability": "supplied", "required": True} for c in channels]
    solve = add_solve(
        db_session, world,
        fits=[fit_spec(c) for c in channels],
        solve_target=target(world, outputs=outputs),
        product_sets=[{"key": "all", "members": [(f"d_{c}", []) for c in channels]}],
    )
    result = select(db_session, bundle_request(world, *channels, scope=Scope.full_network))
    assert result.outcome is Outcome.sole_eligible_candidate and result.selected_ref == f"{solve.public_ref}/all"
    assert isinstance(NetworkRule, type)


def test_nodes_that_hold_no_member_to_judge_are_never_ranked_by_a_rule(db_session, world):
    """Two nodes whose only requested output is a declared zero: nothing about them can be judged, so no edge."""
    zero = {"channel_key": "elim", "availability": "declared_zero", "zero_basis": "source_statement", "required": True}
    for reduction in (A_, B_):
        add_solve(
            db_session,
            world,
            fits=[fit_spec("assoc")],
            solve_target=target(world, outputs=[{"channel_key": "assoc", "availability": "supplied", "required": True}, zero]),
            product_sets=[{"key": "p", "members": [("d_assoc", [])]}],
            protocol=protocol(reduction),
        )
    result = select(db_session, bundle_request(world, "elim"), [ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_)])
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert result.decision.edges == () and result.decision.pair_checks == ()
    (match,) = result.decision.rule_matches
    sides = {c["node_ref"]: c["preferred"] for c in match["candidates"]}
    assert {s["state"] for s in sides.values()} == {"unknown"}  # nothing to judge is unknown, never true
