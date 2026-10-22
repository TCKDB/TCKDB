"""The domain-neutral selection kernel, exercised on plain refs with no thermo or kinetics type in sight.

TCKDB ranks no method above another, so the kernel has three outcomes (none, sole, incomparable alternatives in one
front) and an administrative order. The H298 selector's own behaviour through it is pinned by
``tests/services/thermo_selection/test_h298_golden.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import (
    ADMIN_FIRST_BASIS,
    BASIS_INCOMPARABLE,
    BASIS_NONE_ELIGIBLE,
    BASIS_SOLE,
    AdminNode,
    Outcome,
    decide_alternatives,
    order_admin,
)

T0 = datetime(2026, 6, 1)


def node(ref, rank, *, status=S.approved, age=0, key=None):
    return AdminNode(ref=ref, id_rank=rank, review_status=status, created_at=T0 - timedelta(days=age), admin_key=key)


def decide(nodes, *, policy=SelectionPolicy.default):
    return decide_alternatives(nodes, admin_policy=policy)


ABC = [node("a", 1), node("b", 2), node("c", 3)]


def test_outcome_has_exactly_the_four_values_a_method_free_selection_can_reach():
    assert {o.value for o in Outcome} == {
        "incomparable_alternatives", "sole_eligible_candidate", "no_applicable_candidate", "bounded_search_exceeded"}


def test_several_nodes_are_one_front_of_incomparable_alternatives_and_the_first_is_administrative():
    verdict = decide(ABC)
    assert verdict.outcome is Outcome.incomparable_alternatives and verdict.selected_ref is None
    assert verdict.basis == BASIS_INCOMPARABLE
    assert len(verdict.fronts) == 1 and set(verdict.fronts[0]) == {"a", "b", "c"}
    assert verdict.fronts[0] == verdict.administrative_order
    assert verdict.administrative_first_ref == verdict.fronts[0][0]
    assert "not a claim" in ADMIN_FIRST_BASIS


def test_a_single_node_is_sole_not_preferred():
    verdict = decide(ABC[:1])
    assert verdict.outcome is Outcome.sole_eligible_candidate and verdict.selected_ref == "a"
    assert verdict.basis == BASIS_SOLE and verdict.administrative_first_ref is None


def test_no_nodes_is_no_applicable_candidate():
    verdict = decide([])
    assert verdict.outcome is Outcome.no_applicable_candidate and verdict.selected_ref is None
    assert verdict.basis == BASIS_NONE_ELIGIBLE and verdict.fronts == () and verdict.administrative_order == ()


def test_review_rank_orders_by_default_under_review_ties_not_reviewed_and_latest_ignores_review():
    nodes = [
        node("unreviewed-new", 1, status=S.not_reviewed, age=1),
        node("approved-old", 2, status=S.approved, age=300),
        node("under-review-mid", 3, status=S.under_review, age=50),
    ]
    assert [n.ref for n in order_admin(nodes, SelectionPolicy.default)] == ["approved-old", "unreviewed-new", "under-review-mid"]
    assert [n.ref for n in order_admin(nodes, SelectionPolicy.latest)] == ["unreviewed-new", "under-review-mid", "approved-old"]


def test_an_adapter_supplied_admin_key_replaces_the_policy_key_and_must_cover_every_node():
    keyed = [node("a", 1, key=(2,)), node("b", 2, key=(1,))]
    assert [n.ref for n in order_admin(keyed, SelectionPolicy.default)] == ["b", "a"]
    with pytest.raises(ValueError, match="admin_key"):
        order_admin([node("a", 1, key=(1,)), node("b", 2)], SelectionPolicy.default)


def test_repeated_refs_and_repeated_ranks_are_refused():
    with pytest.raises(ValueError, match="refs"):
        decide([node("a", 1), node("a", 2)])
    with pytest.raises(ValueError, match="id_rank"):
        decide([node("a", 1), node("b", 1)])


def test_input_order_does_not_change_the_verdict():
    assert decide(ABC) == decide(list(reversed(ABC)))
