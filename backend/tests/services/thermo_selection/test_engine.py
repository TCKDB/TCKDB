"""The thermo ordering engine: no method ranks one candidate above another; one front in administrative order."""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.thermo_selection import engine
from app.services.thermo_selection.models import NormalizedCandidate, Outcome
from tests.services.thermo_selection._support import cand, protocol


def decide(candidates, *, policy=SelectionPolicy.default):
    return engine.decide(candidates, admin_policy=policy)


# -- the three outcomes -------------------------------------------------------------------------


def test_one_eligible_candidate_is_a_sole_candidate_not_a_comparative_win():
    decision = decide([cand("only", proto="g3")])
    assert decision.outcome is Outcome.sole_eligible_candidate
    assert decision.selected_ref == "only" and decision.basis == engine.BASIS_SOLE
    assert decision.administrative_first is None


def test_no_eligible_candidate_is_no_applicable_candidate():
    decision = decide([])
    assert decision.outcome is Outcome.no_applicable_candidate and decision.selected_ref is None
    assert decision.fronts == () and decision.administrative_order == ()


def test_several_eligible_candidates_are_incomparable_whatever_they_declare():
    """A newer G3 and an older G4 used to be ordered by a method rule. TCKDB carries none: they tie, in one front."""
    g4 = cand("g4", proto="g4", age_days=400, id_rank=1)
    g3 = cand("g3", proto="g3", age_days=1, id_rank=2)
    for policy in SelectionPolicy:
        decision = decide([g4, g3], policy=policy)
        assert decision.outcome is Outcome.incomparable_alternatives, policy
        assert decision.selected_ref is None
        assert decision.basis == engine.BASIS_INCOMPARABLE
        assert len(decision.fronts) == 1 and set(decision.fronts[0]) == {"g4", "g3"}
        assert decision.administrative_first == {"thermo_ref": decision.fronts[0][0], "basis": engine.ADMIN_FIRST_BASIS}


def test_the_front_is_the_administrative_order_and_the_latest_policy_puts_the_newer_record_first():
    g4 = cand("g4", proto="g4", age_days=400, id_rank=1)
    g3 = cand("g3", proto="g3", age_days=1, id_rank=2)
    assert decide([g4, g3], policy=SelectionPolicy.latest).fronts == (("g3", "g4"),)
    assert decide([g4, g3], policy=SelectionPolicy.default).fronts == (("g3", "g4"),)  # same review rank: newest first


def test_the_three_selection_outcomes_carry_different_explanations():
    sole = decide([cand("a")])
    alternatives = decide([cand("a", proto="g4"), cand("b", proto=None)])
    none = decide([])
    assert len({sole.basis, alternatives.basis, none.basis}) == 3
    assert "no ranking" in alternatives.basis  # says it ranks nothing
    assert alternatives.administrative_first is not None and sole.administrative_first is None


def test_the_first_record_is_never_described_as_better_or_preferred():
    decision = decide([cand("a", proto="g4"), cand("b", proto="g3")])
    text = " ".join([decision.basis, decision.administrative_first["basis"]]).lower()  # type: ignore[index]
    assert "not a claim" in text
    assert "best" not in text.replace("global-best", "")


# -- administrative order -----------------------------------------------------------------------


def test_review_rank_orders_the_front_and_under_review_ties_not_reviewed():
    approved_old = cand("approved-old", status=S.approved, age_days=300, id_rank=1)
    under_review_new = cand("under-review-new", status=S.under_review, age_days=1, id_rank=2)
    unreviewed_mid = cand("unreviewed-mid", status=S.not_reviewed, age_days=50, id_rank=3)
    unreviewed_old = cand("unreviewed-old", status=S.not_reviewed, age_days=500, id_rank=4)
    decision = decide([unreviewed_old, unreviewed_mid, under_review_new, approved_old])
    # Approved first however old; then under_review and not_reviewed together, newest deposited first.
    assert decision.fronts == (("approved-old", "under-review-new", "unreviewed-mid", "unreviewed-old"),)
    assert decide([under_review_new, unreviewed_mid]).fronts == (("under-review-new", "unreviewed-mid"),)
    older_under_review = cand("under-review-old", status=S.under_review, age_days=400, id_rank=5)
    # An older under_review record does not outrank a newer not_reviewed one.
    assert decide([older_under_review, unreviewed_mid]).fronts == (("unreviewed-mid", "under-review-old"),)


def test_latest_orders_by_deposition_time_alone():
    a = cand("a-new-unreviewed", status=S.not_reviewed, age_days=1, id_rank=1)
    b = cand("b-old-approved", status=S.approved, age_days=300, id_rank=2)
    assert decide([a, b], policy=SelectionPolicy.default).fronts == (("b-old-approved", "a-new-unreviewed"),)
    assert decide([a, b], policy=SelectionPolicy.latest).fronts == (("a-new-unreviewed", "b-old-approved"),)


def test_distinct_id_ranks_are_required():
    with pytest.raises(ValueError, match="id_rank"):
        decide([cand("a", id_rank=1), cand("b", id_rank=1)])


def test_refs_must_be_distinct():
    with pytest.raises(ValueError, match="refs"):
        decide([cand("a", id_rank=1), cand("a", id_rank=2)])


def test_decision_is_deterministic_regardless_of_input_order():
    cs = [cand("g4", proto="g4", id_rank=1), cand("g3", proto="g3", id_rank=2), cand("x", proto=None, id_rank=3)]
    assert decide(cs).to_dict() == decide(list(reversed(cs))).to_dict()


def test_the_decision_document_holds_no_rule_edge_or_conflict_field():
    keys = set(decide([cand("a"), cand("b")]).to_dict())
    assert keys == {"outcome", "selected_ref", "basis", "fronts", "administrative_order", "administrative_first"}


def test_normalized_candidates_round_trip_through_their_manifest_form():
    c = cand("g4", proto=protocol("g4", departures=[{"component": "geometry", "description": "d"}]), linked=("g4",))
    assert NormalizedCandidate.from_dict(c.to_dict()) == c
