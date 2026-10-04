"""The thermo preference engine: edges from matching rules, then the shared selection kernel.

Input is a set of *eligible* candidates (physically applicable, above the review floor,
not rejected or deprecated) as :class:`NormalizedCandidate` values; output is a
:class:`Decision` over public refs. It reads no database, so a recorded decision replays
from its manifest alone.

This module owns what is specific to formation enthalpy: which rules apply (active, for this
quantity, scoped to this species) and which candidates sit on each side of one. An edge a -> b
exists only when the rule's preferred side is true for a and its yielding side is true for b;
unknown or false makes no edge, and nothing else, including review status, timestamps, or the
number of attachments, ever creates one. Conflicts, fronts, administrative order and the outcome
are the domain-neutral kernel in :mod:`app.services.selection_kernel`, whose semantics are
documented there.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import (  # noqa: F401  (the BASIS_* sentences are re-exported unchanged)
    ADMIN_FIRST_BASIS,
    BASIS_CONFLICT,
    BASIS_INCOMPARABLE,
    BASIS_INCOMPARABLE_NO_RULE,
    BASIS_NONE_ELIGIBLE,
    BASIS_PREFERRED,
    BASIS_SOLE,
    AdminNode,
    decide_graph,
    order_admin,
)
from app.services.thermo_selection.models import (
    QUANTITY,
    Decision,
    Edge,
    NormalizedCandidate,
    RuleMatch,
    Subject,
    Tri,
)
from app.services.thermo_selection.rules import RULE_ACTIVE, PreferenceRule


def _node(c: NormalizedCandidate) -> AdminNode:
    return AdminNode(ref=c.thermo_ref, id_rank=c.id_rank, review_status=c.review_status, created_at=c.created_at)


def order_within(
    candidates: Sequence[NormalizedCandidate], policy: SelectionPolicy
) -> list[NormalizedCandidate]:
    """Administrative order: the existing ``simple_selection_sort_key`` over the candidate's own fields."""
    ordered = order_admin([_node(c) for c in candidates], policy)  # refuses a repeated id_rank
    by_rank = {c.id_rank: c for c in candidates}
    return [by_rank[n.id_rank] for n in ordered]


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

    verdict = decide_graph(
        [_node(c) for c in candidates],
        edges,
        supersedes={r.rule_id: r.supersedes for r in rules},
        admin_policy=admin_policy,
    )
    first = (
        {"thermo_ref": verdict.administrative_first_ref, "basis": ADMIN_FIRST_BASIS}
        if verdict.administrative_first_ref is not None
        else None
    )
    return Decision(
        outcome=verdict.outcome,
        selected_ref=verdict.selected_ref,
        basis=verdict.basis,
        fronts=verdict.fronts,
        administrative_order=verdict.administrative_order,
        administrative_first=first,
        edges=verdict.edges,
        overridden_edges=verdict.overridden_edges,
        opposing_pairs=verdict.opposing_pairs,
        cycles=verdict.cycles,
        rule_matches=tuple(rule_matches),
        rules=rule_entries,
    )
