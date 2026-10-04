"""The domain-neutral selection kernel, exercised on plain refs with no thermo or kinetics type in sight.

The H298 selector's own behaviour through the kernel is pinned byte-for-byte by
``tests/services/thermo_selection/test_h298_golden.py``; these tests pin the kernel's contract
itself, so the kinetics selector can rely on it without importing anything from thermo.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import (
    ADMIN_FIRST_BASIS,
    AdminNode,
    Edge,
    Outcome,
    decide_graph,
    fronts,
    order_admin,
    resolve_opposing,
    strongly_connected,
)

T0 = datetime(2026, 6, 1)


def node(ref, rank, *, status=S.approved, age=0):
    return AdminNode(ref=ref, id_rank=rank, review_status=status, created_at=T0 - timedelta(days=age))


def edge(a, b, rule="R1", version="1"):
    return Edge(a, b, rule, version)


def decide(nodes, edges, *, supersedes=None, policy=SelectionPolicy.default):
    rules = {e.rule_id for e in edges} | set(supersedes or {})
    table = {r: tuple((supersedes or {}).get(r, ())) for r in rules}
    return decide_graph(nodes, edges, supersedes=table, admin_policy=policy)


ABC = [node("a", 1), node("b", 2), node("c", 3)]


def test_a_chain_has_one_front_per_link_and_selects_its_head():
    verdict = decide(ABC, [edge("a", "b"), edge("b", "c", "R2")])
    assert verdict.outcome is Outcome.policy_preferred and verdict.selected_ref == "a"
    assert verdict.fronts == (("a",), ("b",), ("c",))


def test_two_nodes_with_no_incoming_edge_are_incomparable_and_the_first_is_administrative():
    verdict = decide(ABC, [edge("a", "c")])
    assert verdict.outcome is Outcome.incomparable_alternatives and verdict.selected_ref is None
    assert verdict.fronts[0] == ("a", "b") or verdict.fronts[0] == ("b", "a")
    assert verdict.administrative_first_ref == verdict.fronts[0][0]
    assert ADMIN_FIRST_BASIS  # the caller labels the fallback with this sentence


def test_a_single_node_is_sole_not_preferred():
    verdict = decide([node("a", 1)], [])
    assert verdict.outcome is Outcome.sole_eligible_candidate and verdict.selected_ref == "a"


def test_no_nodes_is_no_applicable_candidate():
    verdict = decide([], [])
    assert verdict.outcome is Outcome.no_applicable_candidate and verdict.fronts == ()


def test_a_cycle_is_a_conflict_with_no_selection_and_every_edge_kept():
    edges = [edge("a", "b"), edge("b", "c", "R2"), edge("c", "a", "R3")]
    verdict = decide(ABC, edges)
    assert verdict.outcome is Outcome.policy_conflict and verdict.selected_ref is None
    assert verdict.cycles == (("a", "b", "c"),) and len(verdict.edges) == 3
    assert verdict.fronts == (verdict.administrative_order,)


def test_a_self_loop_is_a_cycle():
    assert strongly_connected(["a"], {"a": {"a"}}) == [["a"]]


def test_an_opposing_pair_is_a_conflict_unless_one_rule_supersedes_the_other():
    edges = [edge("a", "b", "R1"), edge("b", "a", "R2")]
    unresolved = decide(ABC[:2], edges)
    assert unresolved.outcome is Outcome.policy_conflict
    assert unresolved.opposing_pairs == ({"between": ["a", "b"], "rules": ["R1@1", "R2@1"]},)
    resolved = decide(ABC[:2], edges, supersedes={"R2": ("R1",)})
    assert resolved.outcome is Outcome.policy_preferred and resolved.selected_ref == "b"
    assert [(o["rule_id"], o["overridden_by_rule_id"]) for o in resolved.overridden_edges] == [("R1", "R2")]
    mutual = decide(ABC[:2], edges, supersedes={"R1": ("R2",), "R2": ("R1",)})
    assert mutual.outcome is Outcome.policy_conflict


def test_a_later_version_is_not_supersession():
    edges = [edge("a", "b", "R1", "1"), edge("b", "a", "R2", "9")]
    assert decide(ABC[:2], edges).outcome is Outcome.policy_conflict


def test_resolve_opposing_returns_the_surviving_edges_in_input_order():
    edges = [edge("a", "b", "R1"), edge("b", "a", "R2"), edge("a", "c", "R1")]
    kept, overridden, opposing = resolve_opposing(edges, {"R1": (), "R2": ("R1",)})
    assert kept == [edges[1], edges[2]] and len(overridden) == 1 and opposing == []


def test_administrative_order_sorts_within_a_front_and_never_across_fronts():
    old_approved = node("old", 1, status=S.approved, age=100)
    new_unreviewed = node("new", 2, status=S.not_reviewed, age=0)
    # old precedes new by an edge: recency (latest) puts new first administratively but cannot cross fronts.
    verdict = decide([old_approved, new_unreviewed], [edge("old", "new")], policy=SelectionPolicy.latest)
    assert verdict.administrative_order[0] == "new"
    assert verdict.fronts == (("old",), ("new",)) and verdict.selected_ref == "old"
    # Within one front the policy does decide.
    flat = decide([old_approved, new_unreviewed], [], policy=SelectionPolicy.latest)
    assert flat.fronts == (("new", "old"),) and flat.administrative_first_ref == "new"


def test_review_status_orders_by_default_and_latest_ignores_it():
    best = node("best", 1, status=S.approved, age=50)
    fresh = node("fresh", 2, status=S.not_reviewed, age=0)
    assert [n.ref for n in order_admin([fresh, best], SelectionPolicy.default)] == ["best", "fresh"]
    assert [n.ref for n in order_admin([best, fresh], SelectionPolicy.latest)] == ["fresh", "best"]


def test_a_conflict_elsewhere_blocks_the_whole_request():
    nodes = [node("a", 1), node("b", 2), node("y", 3), node("z", 4)]
    verdict = decide(nodes, [edge("a", "b", "R1"), edge("b", "a", "R2"), edge("z", "y", "R3")])
    assert verdict.outcome is Outcome.policy_conflict


def test_repeated_refs_and_repeated_ranks_are_refused():
    with pytest.raises(ValueError, match="refs must be distinct"):
        decide([node("a", 1), node("a", 2)], [])
    with pytest.raises(ValueError, match="id_rank values must be distinct"):
        decide([node("a", 1), node("b", 1)], [])


def test_fronts_refuses_a_cyclic_graph_rather_than_looping():
    with pytest.raises(ValueError, match="not acyclic"):
        fronts(["a", "b"], {"a": {"b"}, "b": {"a"}})


def test_duplicate_edges_collapse_and_edge_order_does_not_matter():
    edges = [edge("a", "b"), edge("a", "b"), edge("b", "c", "R2")]
    forward = decide(ABC, edges)
    backward = decide(ABC, list(reversed(edges)))
    assert forward == backward and len(forward.edges) == 2
