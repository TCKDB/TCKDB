"""Conflicts, fronts and outcomes over a preference graph. Pure: no database, no domain knowledge.

Order of operations, and what each step may and may not do:

1. **Edges** arrive already built by the caller's rules: an edge a -> b exists only when a rule
   said so. Nothing in this module, including review status, timestamps or counts, creates one.
2. **Conflicts.** An opposing pair (a -> b under one rule, b -> a under another) resolves only by
   a rule that declares it supersedes the other. Anything left over, and any directed cycle, is a
   ``policy_conflict``: no selection, no fallback, no edge dropped, no tie broken by recency.
3. **Fronts.** For an acyclic graph, front 1 is every node with no incoming edge; remove it and
   repeat. A node the rules say nothing about has no incoming edge, so it stays in front 1 and
   prevents a unique winner.
4. **Administrative order** (review status, then recency or the requested policy, then id order,
   via ``simple_selection_sort_key``) sorts *within* a front. It cannot move a node across
   fronts, so it cannot reverse a preference.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import SelectionPolicy, simple_selection_sort_key
from app.services.selection_kernel.vocabulary import Edge, Outcome

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


@dataclass(frozen=True)
class AdminNode:
    """What the administrative order needs about one node: its public ref and its own sort fields.

    ``id_rank`` is the node's position when the population is ordered by database id; it stands in
    for the id as the last tie-break and is not an id.

    ``admin_key`` is the optional adapter-supplied administrative key: a tuple of already-signed sortable
    values (direction is baked in by the adapter, which names and versions it). When every node of a population
    carries one, it replaces the ``SelectionPolicy`` key; a population that mixes keyed and unkeyed nodes is a
    caller bug and is refused. Adapters that pass none (thermo, kinetics, network) are unaffected.
    """

    ref: str
    id_rank: int
    review_status: RecordReviewStatus
    created_at: datetime
    admin_key: tuple | None = None


def order_admin(nodes: Sequence[AdminNode], policy: SelectionPolicy) -> list[AdminNode]:
    """Administrative order: the existing ``simple_selection_sort_key`` over the node's own fields."""
    by_rank = {n.id_rank: n for n in nodes}
    if len(by_rank) != len(nodes):
        raise ValueError("candidate id_rank values must be distinct")
    keyed = [n for n in nodes if n.admin_key is not None]
    if keyed:
        if len(keyed) != len(nodes):
            raise ValueError("either every node carries an admin_key or none does")
        return sorted(nodes, key=lambda n: (n.admin_key, n.id_rank))
    statuses = {n.id_rank: n.review_status for n in nodes}
    created = {n.id_rank: n.created_at for n in nodes}
    return sorted(
        nodes,
        key=lambda n: simple_selection_sort_key(
            n.id_rank, policy=policy, review_status_by_id=statuses, created_at_by_id=created
        ),
    )


def strongly_connected(nodes: list[str], adjacency: Mapping[str, set[str]]) -> list[list[str]]:
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


def fronts(nodes: list[str], adjacency: Mapping[str, set[str]]) -> list[list[str]]:
    """Successive fronts of an acyclic graph: front 1 has no incoming edge, remove it, repeat."""
    incoming: dict[str, int] = dict.fromkeys(nodes, 0)
    for targets in adjacency.values():
        for target in targets:
            incoming[target] += 1
    remaining = set(nodes)
    result: list[list[str]] = []
    while remaining:
        front = sorted(n for n in remaining if incoming[n] == 0)
        if not front:  # Unreachable for an acyclic graph; refuse rather than loop.
            raise ValueError("preference graph is not acyclic")
        result.append(front)
        for node in front:
            remaining.discard(node)
            for target in adjacency.get(node, ()):
                incoming[target] -= 1
    return result


def resolve_opposing(
    edges: list[Edge], supersedes: Mapping[str, Collection[str]]
) -> tuple[list[Edge], list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop every edge a rule that opposes it declares it supersedes, then report what is still opposed.

    ``supersedes`` maps a rule id to the ids of the rules whose opposing preference it overrides. An opposing
    pair is a -> b under one rule and b -> a under another (or the same) rule.

    Supersession is settled **first, across every opposing pair**, and only the edges that survive it can be
    in conflict. A rule that is superseded is gone, not merely outvoted: with R1 a -> b, R2 b -> a superseding
    R1, and R4 b -> a, R1 is removed, R2 and R4 agree, and nothing is in conflict. (Judging each pair on its
    own, R1 against R4 would look unresolved even though R1 is already out.)

    Returns ``(kept edges, overridden edge records, unresolved opposing pairs)``.
    """
    by_pair: dict[tuple[str, str], list[Edge]] = defaultdict(list)
    for edge in edges:
        by_pair[(edge.preferred, edge.dispreferred)].append(edge)
    pairs: list[tuple[Edge, Edge]] = []
    for (a, b), forward in sorted(by_pair.items()):
        if a > b:
            continue  # each opposing pair is visited once, from its lexicographically smaller end
        for f in forward:
            for r in by_pair.get((b, a), ()):
                pairs.append((f, r))

    overridden: list[dict[str, Any]] = []
    dropped: set[Edge] = set()
    recorded: set[tuple[Edge, str, str]] = set()
    for f, r in pairs:
        f_over_r = r.rule_id in supersedes[f.rule_id]
        r_over_f = f.rule_id in supersedes[r.rule_id]
        if r_over_f and not f_over_r:
            loser, winner = f, r
        elif f_over_r and not r_over_f:
            loser, winner = r, f
        else:
            continue
        dropped.add(loser)
        if (loser, winner.rule_id, winner.rule_version) not in recorded:
            recorded.add((loser, winner.rule_id, winner.rule_version))
            overridden.append({
                **loser.to_dict(),
                "overridden_by_rule_id": winner.rule_id,
                "overridden_by_rule_version": winner.rule_version,
            })

    unresolved: set[tuple[tuple[str, str], tuple[str, ...]]] = set()
    for f, r in pairs:
        if f in dropped or r in dropped:
            continue
        pair_rules = tuple(sorted({f"{f.rule_id}@{f.rule_version}", f"{r.rule_id}@{r.rule_version}"}))
        unresolved.add(((f.preferred, f.dispreferred), pair_rules))
    kept = [e for e in edges if e not in dropped]
    opposing = [{"between": list(pair), "rules": list(pair_rules)} for pair, pair_rules in sorted(unresolved)]
    return kept, overridden, opposing


@dataclass(frozen=True)
class GraphVerdict:
    """The kernel's answer over one set of eligible nodes (public refs only).

    ``administrative_first_ref`` is set only for ``incomparable_alternatives``; the caller labels it.
    """

    outcome: Outcome
    selected_ref: str | None
    basis: str
    fronts: tuple[tuple[str, ...], ...]
    administrative_order: tuple[str, ...]
    administrative_first_ref: str | None
    edges: tuple[Edge, ...]
    overridden_edges: tuple[dict[str, Any], ...]
    opposing_pairs: tuple[dict[str, Any], ...]
    cycles: tuple[tuple[str, ...], ...]


def decide_graph(
    nodes: Sequence[AdminNode],
    edges: Sequence[Edge],
    *,
    supersedes: Mapping[str, Collection[str]],
    admin_policy: SelectionPolicy,
) -> GraphVerdict:
    """Conflicts, fronts and outcome over ``nodes`` and the edges the caller's rules produced."""
    nodes = list(nodes)
    by_ref = {n.ref: n for n in nodes}
    if len(by_ref) != len(nodes):
        raise ValueError("candidate refs must be distinct")
    admin_all = order_admin(nodes, admin_policy)
    admin_refs = tuple(n.ref for n in admin_all)

    ordered_edges = sorted(set(edges), key=lambda e: (e.preferred, e.dispreferred, e.rule_id))
    kept, overridden, opposing = resolve_opposing(ordered_edges, supersedes)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in kept:
        adjacency[edge.preferred].add(edge.dispreferred)
    refs = sorted(by_ref)
    cycles = strongly_connected(refs, adjacency)

    def build(outcome: Outcome, selected: str | None, basis: str, groups: list[list[str]],
              first: str | None = None) -> GraphVerdict:
        return GraphVerdict(
            outcome=outcome, selected_ref=selected, basis=basis,
            fronts=tuple(tuple(g) for g in groups), administrative_order=admin_refs,
            administrative_first_ref=first, edges=tuple(kept),
            overridden_edges=tuple(overridden), opposing_pairs=tuple(opposing),
            cycles=tuple(tuple(c) for c in cycles),
        )

    if not nodes:
        return build(Outcome.no_applicable_candidate, None, BASIS_NONE_ELIGIBLE, [])
    if opposing or cycles:
        # No selection, no fallback. The alternatives stay visible in administrative order, unranked.
        return build(Outcome.policy_conflict, None, BASIS_CONFLICT, [list(admin_refs)])

    groups = [
        [n.ref for n in order_admin([by_ref[r] for r in front], admin_policy)] for front in fronts(refs, adjacency)
    ]
    if len(nodes) == 1:
        return build(Outcome.sole_eligible_candidate, groups[0][0], BASIS_SOLE, groups)
    if len(groups[0]) == 1:
        return build(Outcome.policy_preferred, groups[0][0], BASIS_PREFERRED, groups)
    basis = BASIS_INCOMPARABLE if kept else BASIS_INCOMPARABLE_NO_RULE
    return build(Outcome.incomparable_alternatives, None, basis, groups, groups[0][0])
