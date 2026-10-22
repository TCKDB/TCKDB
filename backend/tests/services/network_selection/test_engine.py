"""The network ordering engine over eligible nodes, on the shared kernel.

TCKDB carries no method-versus-method ranking: one eligible node is a sole eligible candidate, several are
incomparable alternatives in one front in administrative order, none is no applicable candidate.
"""

from __future__ import annotations

from datetime import timedelta

from app.db.models.common import RecordReviewStatus
from app.services.network_selection import Scope, select_network
from app.services.selection_kernel import Outcome
from tests.services.network_selection._protocol import protocol
from tests.services.network_selection._requests import bundle_request, channel_request
from tests.services.network_selection._world import add_solve, fit_spec, set_review, target

A_, B_ = "chemically_significant_eigenvalues", "modified_strong_collision"


def select(session, request):
    return select_network(session, request=request, require_snapshot=False)


def solve_with(db_session, world, reduction: str | None, **changes):
    options = {"fits": [fit_spec("assoc")]}
    if reduction is not None:
        options["protocol"] = protocol(reduction)
    options.update(changes)
    return add_solve(db_session, world, **options)


def ref(solve, key: str = "d_assoc") -> str:
    return solve._dets[key].public_ref


def test_eligible_alternatives_stay_unranked_in_one_front_whatever_they_declare(db_session, world):
    s, t = solve_with(db_session, world, A_), solve_with(db_session, world, B_)
    result = select(db_session, channel_request(world))
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert [set(front) for front in result.decision.fronts] == [{ref(s), ref(t)}]
    assert "no ranking" in result.decision.basis
    assert not {"edges", "rules", "rule_matches", "pair_checks", "rules_applied"} & set(result.decision.to_dict())


def test_no_eligible_candidate_and_a_sole_candidate_are_their_own_outcomes(db_session, world):
    none = select(db_session, channel_request(world))
    assert none.outcome is Outcome.no_applicable_candidate and none.selected_ref is None
    only = solve_with(db_session, world, None)
    sole = select(db_session, channel_request(world))
    assert sole.outcome is Outcome.sole_eligible_candidate and sole.selected_ref == ref(only)
    assert sole.selection_basis == "sole_eligible_candidate"  # not a comparative accuracy statement
    assert "not a comparative or global-best claim" in sole.decision.basis


def test_an_administrative_first_is_labelled_and_never_changes_the_outcome(db_session, world):
    s = solve_with(db_session, world, A_)
    t = solve_with(db_session, world, B_)
    unranked = select(db_session, channel_request(world, first_only=True))
    assert unranked.outcome is Outcome.incomparable_alternatives  # the outcome is kept
    assert unranked.selection_basis == "administrative_first"
    assert unranked.decision.administrative_first["basis"].startswith("first in display order")
    assert unranked.decision.administrative_first["node_ref"] in {ref(s), ref(t)}
    plain = select(db_session, channel_request(world))
    assert plain.selection_basis is None and plain.decision.administrative_first is not None


def test_the_front_is_the_administrative_order_review_rank_then_newest(db_session, world):
    old = solve_with(db_session, world, A_, review=RecordReviewStatus.not_reviewed)
    new = solve_with(db_session, world, B_, review=None)
    new.created_at = old.created_at + timedelta(days=30)  # before it is approved: an accepted row is immutable
    db_session.flush()
    set_review(db_session, world, new, RecordReviewStatus.approved)
    result = select(db_session, channel_request(world))
    assert result.decision.administrative_order == (ref(new), ref(old))
    assert result.decision.fronts == ((ref(new), ref(old)),)
    assert result.selected_ref is None


def test_each_solve_answers_a_different_window_and_no_cross_window_winner_is_formed(db_session, world):
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
    at_low = select(db_session, channel_request(world, temperature_min_k=400.0, temperature_max_k=800.0))
    assert at_low.outcome is Outcome.sole_eligible_candidate and at_low.selected_ref == ref(low)
    at_high = select(db_session, channel_request(world, temperature_min_k=1100.0, temperature_max_k=1900.0))
    assert at_high.outcome is Outcome.sole_eligible_candidate and at_high.selected_ref == ref(high)
    across = select(db_session, channel_request(world, temperature_min_k=400.0, temperature_max_k=1900.0))
    assert across.outcome is Outcome.no_applicable_candidate  # no averaging, no stitching


def test_two_bundle_nodes_are_incomparable_alternatives(db_session, world):
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
    result = select(db_session, bundle_request(world))
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert {n for front in result.decision.fronts for n in front} == {f"{s.public_ref}/both", f"{t.public_ref}/both"}


def test_alternate_fits_are_listed_in_display_order_rank_no_solve_and_are_never_independent(db_session, world):
    solve = add_solve(
        db_session,
        world,
        fits=[
            fit_spec("assoc", rep="plog1"),
            fit_spec("assoc", rep="cheb1", model="chebyshev", pmin=0.01, pmax=100.0),
        ],
        protocol=protocol(A_),
    )
    result = select(db_session, channel_request(world))
    (row,) = result.decision.representations
    assert row["outcome"] == "incomparable_alternatives" and row["chosen_fit_ref"] is None
    assert len(row["fronts"]) == 1 and len(row["fronts"][0]) == 2
    assert "not a claim that one fit is more accurate" in row["basis"] and row["independent_confirmation"] is False
    assert row["shared_provenance"] == {"solve_ref": solve.public_ref}
    assert "edges" not in row and "unused_edges" not in row
    assert result.outcome is Outcome.sole_eligible_candidate  # one solve; the fits ranked no solve


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
