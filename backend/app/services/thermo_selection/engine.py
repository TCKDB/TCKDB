"""The preference engine: edges from matching rules, conflict detection, fronts, outcomes.

Input is a set of *eligible* candidates (physically applicable, above the review floor,
not rejected or deprecated) as :class:`NormalizedCandidate` values; output is a
:class:`Decision` over public refs. It reads no database, so a recorded decision replays
from its manifest alone.

Order of operations, and what each step may and may not do:

1. **Edges.** For each active rule whose scope contains the species, an edge a -> b exists
   only when the rule's preferred side is true for a and its yielding side is true for b.
   Unknown or false makes no edge. Nothing else, including review status, timestamps, or the
   number of attachments, ever creates one.
2. **Conflicts.** An opposing pair (a -> b under one rule, b -> a under another) resolves only
   by a rule that declares it supersedes the other. Anything left over, and any directed cycle,
   is a ``policy_conflict``: no selection, no fallback, no edge dropped, no tie broken by recency.
3. **Fronts.** For an acyclic graph, front 1 is every candidate with no incoming edge; remove
   it and repeat. Candidates the rules say nothing about (missing provenance, experimental or
   estimated records) have no incoming edge, so they stay in front 1 and prevent a unique winner.
4. **Administrative order** (review status, then recency or the requested policy, then id order,
   via ``simple_selection_sort_key``) sorts *within* a front. It cannot move a candidate across
   fronts, so it cannot reverse a preference.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from app.schemas.reads.scientific_common import SelectionPolicy, simple_selection_sort_key
from app.services.thermo_selection.models import (
    QUANTITY,
    Decision,
    Edge,
    NormalizedCandidate,
    Outcome,
    RuleMatch,
    Subject,
    Tri,
)
from app.services.thermo_selection.rules import RULE_ACTIVE, PreferenceRule

BASIS_NONE_ELIGIBLE = "no candidate is eligible for this request"
BASIS_CONFLICT = (
    "opposing rules or a preference cycle make the method order self-contradictory; no automatic selection"
)
BASIS_SOLE = "the only eligible candidate in the searched corpus; not a comparative or global-best claim"
BASIS_PREFERRED = (
    "the only candidate with no incoming preference edge: every other eligible candidate is reached "
    "from it by accepted rule preferences"
)
BASIS_INCOMPARABLE_NO_RULE = (
    "no registered rule compares these eligible candidates; they are unranked, not equally accurate"
)
BASIS_INCOMPARABLE = (
    "more than one candidate has no incoming preference edge; the scientific rules do not rank them"
)
ADMIN_FIRST_BASIS = (
    "administrative order among unresolved first-front alternatives; not a claim that the record is method-superior"
)


def order_within(
    candidates: Sequence[NormalizedCandidate], policy: SelectionPolicy
) -> list[NormalizedCandidate]:
    """Administrative order: the existing ``simple_selection_sort_key`` over the candidate's own fields."""
    by_rank = {c.id_rank: c for c in candidates}
    if len(by_rank) != len(candidates):
        raise ValueError("candidate id_rank values must be distinct")
    statuses = {c.id_rank: c.review_status for c in candidates}
    created = {c.id_rank: c.created_at for c in candidates}
    return sorted(
        candidates,
        key=lambda c: simple_selection_sort_key(
            c.id_rank, policy=policy, review_status_by_id=statuses, created_at_by_id=created
        ),
    )


def _strongly_connected(nodes: list[str], adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components with more than one node, or one node with a self-loop (iterative Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(adjacency.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(adjacency.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in adjacency.get(node, ()):
                    components.append(sorted(component))
    return sorted(components)


def _fronts(nodes: list[str], adjacency: dict[str, set[str]]) -> list[list[str]]:
    incoming: dict[str, int] = dict.fromkeys(nodes, 0)
    for targets in adjacency.values():
        for target in targets:
            incoming[target] += 1
    remaining = set(nodes)
    fronts: list[list[str]] = []
    while remaining:
        front = sorted(n for n in remaining if incoming[n] == 0)
        if not front:  # Unreachable for an acyclic graph; refuse rather than loop.
            raise ValueError("preference graph is not acyclic")
        fronts.append(front)
        for node in front:
            remaining.discard(node)
            for target in adjacency.get(node, ()):
                incoming[target] -= 1
    return fronts


def _resolve_opposing(
    edges: list[Edge], rules: dict[str, PreferenceRule]
) -> tuple[list[Edge], list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop an edge only when an opposing edge's rule declares it supersedes the edge's rule.

    Returns ``(kept edges, overridden edge records, unresolved opposing pairs)``. An opposing pair
    is a -> b under one rule and b -> a under another (or the same) rule.
    """
    by_pair: dict[tuple[str, str], list[Edge]] = defaultdict(list)
    for edge in edges:
        by_pair[(edge.preferred, edge.dispreferred)].append(edge)
    overridden: list[dict[str, Any]] = []
    unresolved: set[tuple[tuple[str, str], tuple[str, ...]]] = set()
    dropped: set[Edge] = set()
    for (a, b), forward in sorted(by_pair.items()):
        if a > b:
            continue  # each opposing pair is visited once, from its lexicographically smaller end
        for f in forward:
            for r in by_pair.get((b, a), ()):
                f_over_r = r.rule_id in rules[f.rule_id].supersedes
                r_over_f = f.rule_id in rules[r.rule_id].supersedes
                if r_over_f and not f_over_r:
                    loser, winner = f, r
                elif f_over_r and not r_over_f:
                    loser, winner = r, f
                else:
                    pair_rules = tuple(sorted({f"{f.rule_id}@{f.rule_version}", f"{r.rule_id}@{r.rule_version}"}))
                    unresolved.add(((a, b), pair_rules))
                    continue
                dropped.add(loser)
                overridden.append({
                    **loser.to_dict(),
                    "overridden_by_rule_id": winner.rule_id,
                    "overridden_by_rule_version": winner.rule_version,
                })
    kept = [e for e in edges if e not in dropped]
    opposing = [{"between": list(pair), "rules": list(pair_rules)} for pair, pair_rules in sorted(unresolved)]
    return kept, overridden, opposing


def decide(
    candidates: Sequence[NormalizedCandidate],
    *,
    subject: Subject,
    admin_policy: SelectionPolicy,
    rules: Sequence[PreferenceRule],
) -> Decision:
    """Decide over eligible candidates. Pure; see the module docstring for the semantics."""
    candidates = list(candidates)
    by_ref = {c.thermo_ref: c for c in candidates}
    if len(by_ref) != len(candidates):
        raise ValueError("candidate refs must be distinct")
    rule_by_id = {r.rule_id: r for r in rules}
    if len(rule_by_id) != len(rules):
        raise ValueError("rule ids must be distinct")
    admin_all = order_within(candidates, admin_policy)
    admin_refs = tuple(c.thermo_ref for c in admin_all)
    rule_entries = tuple(r.describe() for r in rules)

    edges: list[Edge] = []
    rule_matches: list[dict[str, Any]] = []
    for rule in rules:
        header = {"rule_id": rule.rule_id, "rule_version": rule.version}
        if rule.status != RULE_ACTIVE:
            rule_matches.append({**header, "applied": False, "why": f"rule_status_{rule.status}"})
            continue
        if rule.quantity != QUANTITY:
            rule_matches.append({**header, "applied": False, "why": f"rule_quantity_{rule.quantity}"})
            continue
        scope: RuleMatch = rule.scope(subject)
        if scope.state is not Tri.true:
            why = "scope_unknown" if scope.state is Tri.unknown else "outside_rule_scope"
            rule_matches.append({**header, "applied": False, "why": why, "scope": scope.to_dict()})
            continue
        sides = {c.thermo_ref: (rule.preferred_side(c), rule.yielding_side(c)) for c in admin_all}
        preferred = [ref for ref, (p, _) in sides.items() if p.state is Tri.true]
        yielding = [ref for ref, (_, y) in sides.items() if y.state is Tri.true]
        for a in preferred:
            for b in yielding:
                if a != b:
                    edges.append(Edge(a, b, rule.rule_id, rule.version))
        rule_matches.append({
            **header,
            "applied": True,
            "scope": scope.to_dict(),
            "candidates": [
                {"thermo_ref": ref, "preferred": p.to_dict(), "yielding": y.to_dict()}
                for ref, (p, y) in sorted(sides.items())
            ],
        })

    edges = sorted(set(edges), key=lambda e: (e.preferred, e.dispreferred, e.rule_id))
    kept, overridden, opposing = _resolve_opposing(edges, rule_by_id)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in kept:
        adjacency[edge.preferred].add(edge.dispreferred)
    nodes = sorted(by_ref)
    cycles = _strongly_connected(nodes, adjacency)

    def build(outcome: Outcome, selected: str | None, basis: str, fronts: list[list[str]],
              admin_first: dict[str, str] | None = None) -> Decision:
        return Decision(
            outcome=outcome, selected_ref=selected, basis=basis,
            fronts=tuple(tuple(f) for f in fronts), administrative_order=admin_refs,
            administrative_first=admin_first, edges=tuple(kept),
            overridden_edges=tuple(overridden), opposing_pairs=tuple(opposing),
            cycles=tuple(tuple(c) for c in cycles), rule_matches=tuple(rule_matches), rules=rule_entries,
        )

    if not candidates:
        return build(Outcome.no_applicable_candidate, None, BASIS_NONE_ELIGIBLE, [])
    if opposing or cycles:
        # No selection, no fallback. The alternatives stay visible in administrative order, unranked.
        return build(Outcome.policy_conflict, None, BASIS_CONFLICT, [list(admin_refs)])

    raw_fronts = _fronts(nodes, adjacency)
    fronts = [[c.thermo_ref for c in order_within([by_ref[r] for r in front], admin_policy)] for front in raw_fronts]
    if len(candidates) == 1:
        return build(Outcome.sole_eligible_candidate, fronts[0][0], BASIS_SOLE, fronts)
    if len(fronts[0]) == 1:
        return build(Outcome.policy_preferred, fronts[0][0], BASIS_PREFERRED, fronts)
    basis = BASIS_INCOMPARABLE if kept else BASIS_INCOMPARABLE_NO_RULE
    first = {"thermo_ref": fronts[0][0], "basis": ADMIN_FIRST_BASIS}
    return build(Outcome.incomparable_alternatives, None, basis, fronts, first)
