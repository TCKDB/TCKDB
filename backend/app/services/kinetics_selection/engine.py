"""The kinetics preference engine: edges from matching rules, then the shared selection kernel.

Input is the *eligible* determinations (each with all of its eligible representations) and the request;
output is a :class:`KineticsDecision` over public refs. It reads no database, so a recorded decision replays
from its manifest alone.

What is specific to kinetics, and lives here and not in the kernel:

* **The competition unit is a determination.** A rule is judged on every representation of both sides. An
  edge a -> b exists only if *every* representation of a is on the rule's preferred side, *every*
  representation of b on its yielding side, and *every* pair of representations is verified compatible on
  everything else the rule does not compare. One unknown or opposing representation prevents the edge, so
  alternate fits are never independent confirmation and never a way around a missing fact.
* **Edges compose only under one objective.** A barrier-only comparison, a tunneling-only comparison and a
  comparison on a disjoint temperature window cannot be chained into a preference about the whole rate. If
  edges arise under more than one objective, none of them is composed: the determinations stay unranked
  (``incomparable_alternatives``) and the edges are reported as unused, with the reason.
* **A rule applies only when active, and only to its scope.** An inactive rule appears in the decision with
  its reasons and makes no edge. An unknown prerequisite makes no edge either.
* **A request that does not apply rules applies none.** ``apply_rules`` is false for every administrative policy
  (``default``, ``most_reviewed``, ``latest``); the determinations are then in the plain administrative order.
* **A rule names its objective.** An empty ``objective_key`` is refused: two rules with no stated objective must not
  be read as comparing the same thing and composed.
* **Supersession is within one objective.** Rules that arise under more than one objective are not composed at all,
  so a rule that supersedes another but compares a different objective never overrides it: both sets of edges are
  reported as unused and the determinations stay unranked. This is policy, not an omission: a precedence across
  objectives would itself be a claim about which part of a rate matters more, and no council or curator has made it.

Conflicts, fronts and outcomes are the kernel's (:mod:`app.services.selection_kernel`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.kinetics_selection.grouping import group_by_determination, group_nodes
from app.services.kinetics_selection.models import (
    QUANTITY,
    KineticsRequest,
    KineticsSubject,
    NormalizedKinetics,
)
from app.services.kinetics_selection.rules import EDGE_LABEL, RULE_ACTIVE, KineticsRule
from app.services.selection_kernel import (
    ADMIN_FIRST_BASIS,
    AdminNode,
    Edge,
    Outcome,
    RuleMatch,
    Tri,
    decide_graph,
)

BASIS_OBJECTIVES_NOT_COMPOSED = (
    "preference edges arose under more than one objective; they are not composed into a path, so the eligible "
    "determinations stay unranked"
)


@dataclass(frozen=True)
class DeterminationCandidate:
    """One eligible determination with its eligible representations, in administrative order."""

    determination_ref: str
    records: tuple[NormalizedKinetics, ...]
    node: AdminNode


def build_candidates(
    eligible: Sequence[NormalizedKinetics], *, admin_policy: SelectionPolicy
) -> list[DeterminationCandidate]:
    """Group eligible records by determination, in the administrative order of the determinations."""
    by_ref = {c.kinetics_ref: c for c in eligible}
    groups = group_by_determination(eligible, admin_policy=admin_policy)
    nodes = {n.ref: n for n in group_nodes(groups)}
    return [
        DeterminationCandidate(
            determination_ref=g.determination_ref,
            records=tuple(by_ref[r] for r in g.representation_refs),
            node=nodes[g.determination_ref],
        )
        for g in groups
    ]


@dataclass(frozen=True)
class KineticsDecision:
    """The engine's verdict over a set of eligible determinations (public refs only)."""

    outcome: Outcome
    selected_determination_ref: str | None
    representation_refs: tuple[str, ...]
    basis: str
    fronts: tuple[tuple[str, ...], ...]
    administrative_order: tuple[str, ...]
    administrative_first: dict[str, str] | None
    edges: tuple[dict[str, Any], ...]
    overridden_edges: tuple[dict[str, Any], ...]
    opposing_pairs: tuple[dict[str, Any], ...]
    cycles: tuple[tuple[str, ...], ...]
    unused_edges: tuple[dict[str, Any], ...]
    rule_matches: tuple[dict[str, Any], ...]
    pair_checks: tuple[dict[str, Any], ...]
    rules: tuple[dict[str, Any], ...]
    rules_applied: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "selected_determination_ref": self.selected_determination_ref,
            "representation_refs": list(self.representation_refs),
            "basis": self.basis,
            "fronts": [list(f) for f in self.fronts],
            "administrative_order": list(self.administrative_order),
            "administrative_first": self.administrative_first,
            "edges": [dict(e) for e in self.edges],
            "overridden_edges": [dict(e) for e in self.overridden_edges],
            "opposing_pairs": [dict(p) for p in self.opposing_pairs],
            "cycles": [list(c) for c in self.cycles],
            "unused_edges": [dict(e) for e in self.unused_edges],
            "rule_matches": [dict(m) for m in self.rule_matches],
            "pair_checks": [dict(p) for p in self.pair_checks],
            "rules": [dict(r) for r in self.rules],
            "rules_applied": self.rules_applied,
        }


def _aggregate(matches: Sequence[RuleMatch]) -> RuleMatch:
    """False beats unknown beats true over several verdicts; reasons are kept once each, in order."""
    reasons: list[str] = []
    for m in matches:
        for reason in m.reasons:
            if reason not in reasons:
                reasons.append(reason)
    if any(m.state is Tri.false for m in matches):
        return RuleMatch(Tri.false, tuple(reasons))
    if any(m.state is Tri.unknown for m in matches):
        return RuleMatch(Tri.unknown, tuple(reasons))
    return RuleMatch(Tri.true, tuple(reasons))


def _side(candidate: DeterminationCandidate, judge: Callable[[NormalizedKinetics], RuleMatch]) -> RuleMatch:
    """A determination is on a side only if every one of its representations is."""
    return _aggregate([judge(r) for r in candidate.records])


def _pair(rule: KineticsRule, a: DeterminationCandidate, b: DeterminationCandidate) -> RuleMatch:
    """Two determinations agree on everything the rule does not compare only if every pair of representations does."""
    return _aggregate([rule.compatible(x, y) for x in a.records for y in b.records])


def decide(
    candidates: Sequence[DeterminationCandidate],
    *,
    subject: KineticsSubject,
    request: KineticsRequest,
    rules: Sequence[KineticsRule],
) -> KineticsDecision:
    """Decide over eligible determinations. Pure; see the module docstring for the semantics."""
    candidates = list(candidates)
    by_ref = {c.determination_ref: c for c in candidates}
    if len(by_ref) != len(candidates):
        raise ValueError("determination refs must be distinct")
    applied_rules = tuple(rules) if request.apply_rules else ()
    rule_by_id = {r.rule_id: r for r in applied_rules}
    if len(rule_by_id) != len(applied_rules):
        raise ValueError("rule ids must be distinct")
    unnamed = sorted(r.rule_id for r in applied_rules if not r.objective_key)
    if unnamed:
        raise ValueError(f"every rule names the objective it compares on; no objective_key on {unnamed}")

    edges: list[Edge] = []
    pair_checks: list[dict[str, Any]] = []
    rule_matches: list[dict[str, Any]] = []
    for rule in applied_rules:
        header = {"rule_id": rule.rule_id, "rule_version": rule.version}
        if rule.status != RULE_ACTIVE:
            rule_matches.append(
                {**header, "applied": False, "why": f"rule_status_{rule.status}", "reasons": list(rule.inactive_reasons)}
            )
            continue
        if rule.observable != QUANTITY:
            rule_matches.append({**header, "applied": False, "why": f"rule_observable_{rule.observable}"})
            continue
        scope = rule.scope(subject, request)
        if scope.state is not Tri.true:
            why = "scope_unknown" if scope.state is Tri.unknown else "outside_rule_scope"
            rule_matches.append({**header, "applied": False, "why": why, "scope": scope.to_dict()})
            continue
        sides = {c.determination_ref: (_side(c, rule.preferred_side), _side(c, rule.yielding_side)) for c in candidates}
        for a in candidates:
            for b in candidates:
                if a.determination_ref == b.determination_ref:
                    continue
                if sides[a.determination_ref][0].state is not Tri.true:
                    continue
                if sides[b.determination_ref][1].state is not Tri.true:
                    continue
                compatible = _pair(rule, a, b)
                pair_checks.append(
                    {
                        **header,
                        "preferred": a.determination_ref,
                        "dispreferred": b.determination_ref,
                        "compatible": compatible.to_dict(),
                    }
                )
                if compatible.state is Tri.true:
                    edges.append(Edge(a.determination_ref, b.determination_ref, rule.rule_id, rule.version))
        rule_matches.append(
            {
                **header,
                "applied": True,
                "scope": scope.to_dict(),
                "determinations": [
                    {
                        "determination_ref": ref,
                        "preferred": sides[ref][0].to_dict(),
                        "yielding": sides[ref][1].to_dict(),
                    }
                    for ref in sorted(sides)
                ],
            }
        )

    def edge_record(edge: Edge) -> dict[str, Any]:
        rule = rule_by_id[edge.rule_id]
        return {**edge.to_dict(), "label": EDGE_LABEL, "objective_key": rule.objective_key}

    objectives = sorted({rule_by_id[e.rule_id].objective_key for e in edges})
    nodes = [c.node for c in candidates]
    supersedes = {r.rule_id: r.supersedes for r in applied_rules}
    unused: list[dict[str, Any]] = []
    if len(objectives) > 1:
        unused = [edge_record(e) for e in sorted(set(edges), key=lambda e: (e.preferred, e.dispreferred, e.rule_id))]
        graph_edges: list[Edge] = []
    else:
        graph_edges = edges
    verdict = decide_graph(nodes, graph_edges, supersedes=supersedes, admin_policy=request.admin_policy)
    basis = BASIS_OBJECTIVES_NOT_COMPOSED if unused else verdict.basis
    first = (
        {"determination_ref": verdict.administrative_first_ref, "basis": ADMIN_FIRST_BASIS}
        if verdict.administrative_first_ref is not None
        else None
    )
    selected = verdict.selected_ref
    return KineticsDecision(
        outcome=verdict.outcome,
        selected_determination_ref=selected,
        representation_refs=tuple(r.kinetics_ref for r in by_ref[selected].records) if selected is not None else (),
        basis=basis,
        fronts=verdict.fronts,
        administrative_order=verdict.administrative_order,
        administrative_first=first,
        edges=tuple(edge_record(e) for e in verdict.edges),
        overridden_edges=verdict.overridden_edges,
        opposing_pairs=verdict.opposing_pairs,
        cycles=verdict.cycles,
        unused_edges=tuple(unused),
        rule_matches=tuple(rule_matches),
        pair_checks=tuple(pair_checks),
        rules=tuple(r.describe() for r in applied_rules),
        rules_applied=request.apply_rules,
    )


__all__ = [
    "BASIS_OBJECTIVES_NOT_COMPOSED",
    "DeterminationCandidate",
    "KineticsDecision",
    "build_candidates",
    "decide",
]
