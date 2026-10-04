"""The network preference engine: edges from matching rules, then the shared selection kernel.

Input is the *eligible* decision nodes (a determination with all of its eligible fits, or a declared product set
with its members) and the request; output is a :class:`NetworkDecision` over public refs. It reads no database, so
a recorded decision replays from its manifest alone. Conflicts, fronts and outcomes are the kernel's
(:mod:`app.services.selection_kernel`), exactly as for H298 and elementary kinetics; nothing here changes it.

What is specific to networks, and lives here and not in the kernel:

* **The competition unit is a determination (one channel) or a declared product set (a bundle).** A rule is judged
  on every member of both sides. An edge a -> b exists only if *every* member of a is on the rule's preferred side,
  *every* member of b on its yielding side, and every pair of members answering the same output is verified
  compatible on everything else the rule does not compare. Opposite advantages on different channels therefore
  yield no edge, and no winner: there is no averaging, no weighting, no stitching of temperature windows and no
  splicing of channels across solves.
* **A rule applies only to a request that states its objective.** Physical accuracy, fidelity to a pinned model and
  representation fidelity are different questions; agreement on one never ranks another. A rule of another objective
  is reported as not applied, with why.
* **Edges compose only under one objective key.** If edges arise under more than one, none is composed: the nodes
  stay unranked (``incomparable_alternatives``) and the edges are reported as unused.
* **A rule applies only when active, and only to its scope.** An inactive rule appears in the decision with its
  reasons and makes no edge; an unknown prerequisite makes none either.
* **Administrative policies other than ``method_preferred`` apply no rule.** They are the plain administrative order
  among eligible nodes, which never reverses an accepted edge.
* **Alternate fits are one candidate.** Within a determination, a ``representation_fidelity`` request may compare the
  fits by representation rules; the result is nested fronts that rank no physical solve and add no independent
  confirmation. Any other request leaves the fits in administrative order, said so.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tckdb_schemas.network_declarations import NetworkComparisonObjective

from app.services.network_selection.models import (
    QUANTITY,
    BundleAssessment,
    DeterminationAssessment,
    NetworkFacts,
    NetworkRequest,
    Scope,
    SolveFacts,
)
from app.services.network_selection.rules import (
    EDGE_LABEL,
    LEVEL_CANDIDATE,
    LEVEL_REPRESENTATION,
    RULE_ACTIVE,
    MemberFacts,
    NetworkCandidate,
    NetworkRepresentationRule,
    NetworkRule,
    validate_rules,
)
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
    "candidates stay unranked"
)
BASIS_REPRESENTATIONS_ADMINISTRATIVE = (
    "no representation rule applies to this request; the fits are in administrative order, which is not a claim "
    "that one fit is more accurate"
)

SELECTION_BASIS_SCIENTIFIC = "scientific_preference"
SELECTION_BASIS_SOLE = "sole_eligible_candidate"
SELECTION_BASIS_ADMIN_FIRST = "administrative_first"


def _members_of(solve: SolveFacts, member_assessments: Sequence[tuple[DeterminationAssessment, tuple[str, ...]]]) -> tuple[MemberFacts, ...]:
    by_det = {d.determination_ref: d for d in solve.determinations}
    fit_by_ref = {f.fit_ref: f for f in solve.fits}
    members: list[MemberFacts] = []
    for assessed, fit_refs in member_assessments:
        det = by_det[assessed.determination_ref]
        fits = [fit_by_ref[r] for r in fit_refs]
        members.append(
            MemberFacts(
                determination_ref=det.determination_ref,
                channel_key=det.channel_key,
                observable=det.observable or {},
                fits=tuple(fits),
            )
        )
    return tuple(members)


def build_candidates(
    request: NetworkRequest,
    solves: Sequence[SolveFacts],
    determination_assessments: Sequence[DeterminationAssessment],
    bundle_assessments: Sequence[BundleAssessment],
) -> list[NetworkCandidate]:
    """The eligible decision nodes, from the (recomputed or recorded) assessments and the captured solve facts."""
    solve_by_ref = {s.solve_ref: s for s in solves}
    det_by_ref = {a.determination_ref: a for a in determination_assessments}
    out: list[NetworkCandidate] = []
    if request.scope is Scope.single_channel:
        for assessed in determination_assessments:
            if not assessed.physically_eligible:
                continue
            solve = solve_by_ref[assessed.solve_ref]
            det = next(d for d in solve.determinations if d.determination_ref == assessed.determination_ref)
            out.append(
                NetworkCandidate(
                    node_ref=assessed.determination_ref,
                    scope=request.scope.value,
                    solve=solve,
                    members=_members_of(solve, [(assessed, assessed.eligible_fit_refs)]),
                    node=AdminNode(assessed.determination_ref, det.id_rank, solve.review_status, solve.created_at),
                )
            )
        return out
    for node in bundle_assessments:
        if not node.physically_eligible:
            continue
        solve = solve_by_ref[node.solve_ref]
        members = [(det_by_ref[ref], fits) for ref, fits in node.member_fit_refs]
        out.append(
            NetworkCandidate(
                node_ref=node.node_ref,
                scope=request.scope.value,
                solve=solve,
                members=_members_of(solve, members),
                node=AdminNode(node.node_ref, node.id_rank, solve.review_status, solve.created_at),
            )
        )
    return out


@dataclass(frozen=True)
class NetworkDecision:
    """The engine's verdict over a set of eligible nodes (public refs only).

    ``selected_ref`` is set only for ``policy_preferred`` and ``sole_eligible_candidate``. For
    ``incomparable_alternatives`` the leading front is in ``fronts`` and the administrative first, labelled as
    such, in ``administrative_first``: ``result_mode=first`` may name it, and the outcome stays incomparable.
    Never is a choice made through a conflict.
    """

    outcome: Outcome
    selected_ref: str | None
    selection_basis: str | None
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
    representations: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "selected_ref": self.selected_ref,
            "selection_basis": self.selection_basis,
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
            "representations": [dict(r) for r in self.representations],
        }


def _aggregate(matches: Sequence[RuleMatch]) -> RuleMatch:
    """False beats unknown beats true over several verdicts; reasons are kept once each, in order.

    No verdicts at all is unknown, never true: a side that was judged on nothing has no evidence behind it.
    """
    if not matches:
        return RuleMatch(Tri.unknown, ("nothing_to_judge",))
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


def _side(candidate: NetworkCandidate, judge: Callable[[MemberFacts, SolveFacts], RuleMatch]) -> RuleMatch:
    """A node is on a side only if every one of its members is."""
    return _aggregate([judge(m, candidate.solve) for m in candidate.members])


def _pair(rule: NetworkRule, a: NetworkCandidate, b: NetworkCandidate) -> RuleMatch:
    """Two nodes agree on everything the rule does not compare only if the members answering each output do.

    The members are matched by channel; two nodes that do not answer the same outputs are not comparable here
    (unknown), and for each matched output every pair of eligible fits must be verified compatible.
    """
    left = {m.channel_key: m for m in a.members}
    right = {m.channel_key: m for m in b.members}
    if set(left) != set(right):
        return RuleMatch(Tri.unknown, ("members_answer_different_outputs",))
    checks = [rule.compatible(left[k], a.solve, right[k], b.solve) for k in sorted(left)]
    return _aggregate(checks)


def _applies_to_request(rule: NetworkRule, request: NetworkRequest) -> str | None:
    """Why a rule is not applied to this request, or ``None`` when it is."""
    if rule.status != RULE_ACTIVE:
        return f"rule_status_{rule.status}"
    if rule.level != LEVEL_CANDIDATE:
        return "rule_is_representation_level"
    if rule.observable != QUANTITY:
        return f"rule_observable_{rule.observable}"
    if rule.objective is not request.objective:
        return f"rule_objective_{rule.objective.value}_not_requested"
    return None


def _representations(
    candidates: Sequence[NetworkCandidate], network: NetworkFacts, request: NetworkRequest, rules: Sequence[NetworkRule]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Nested fronts of the fits inside each member of each eligible node. Returns ``(rows, rule_matches)``."""
    fit_rules = [r for r in rules if isinstance(r, NetworkRepresentationRule)]
    rows: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    use_rules = request.apply_rules and request.objective is NetworkComparisonObjective.representation_fidelity
    applied = [r for r in fit_rules if use_rules and r.status == RULE_ACTIVE]
    for rule in fit_rules:
        header = {"rule_id": rule.rule_id, "rule_version": rule.version, "level": LEVEL_REPRESENTATION}
        if rule not in applied:
            why = (
                f"rule_status_{rule.status}"
                if use_rules
                else "request_is_not_representation_fidelity"
            )
            matches.append({**header, "applied": False, "why": why})
    for candidate in candidates:
        for member in candidate.members:
            nodes = [
                AdminNode(f.fit_ref, f.id_rank, candidate.solve.review_status, candidate.solve.created_at) for f in member.fits
            ]
            edges: list[Edge] = []
            for rule in applied:
                scope = rule.scope(network, request)
                if scope.state is not Tri.true:
                    continue
                for a in member.fits:
                    for b in member.fits:
                        if a.fit_ref == b.fit_ref:
                            continue
                        if (
                            rule.fit_preferred(a, candidate.solve).state is Tri.true
                            and rule.fit_yielding(b, candidate.solve).state is Tri.true
                            and rule.fit_compatible(a, b, candidate.solve).state is Tri.true
                        ):
                            edges.append(Edge(a.fit_ref, b.fit_ref, rule.rule_id, rule.version))
            verdict = decide_graph(
                nodes, edges, supersedes={r.rule_id: r.supersedes for r in applied}, admin_policy=request.admin_policy
            )
            rows.append(
                {
                    "node_ref": candidate.node_ref,
                    "determination_ref": member.determination_ref,
                    "outcome": verdict.outcome.value,
                    "fronts": [list(f) for f in verdict.fronts],
                    "administrative_order": list(verdict.administrative_order),
                    "chosen_fit_ref": verdict.selected_ref,
                    "administrative_first": (
                        {"fit_ref": verdict.administrative_first_ref, "basis": ADMIN_FIRST_BASIS}
                        if verdict.administrative_first_ref is not None
                        else None
                    ),
                    "edges": [{**e.to_dict(), "label": EDGE_LABEL} for e in verdict.edges],
                    "basis": verdict.basis if applied else BASIS_REPRESENTATIONS_ADMINISTRATIVE,
                    "shared_provenance": {"solve_ref": candidate.solve.solve_ref},
                    "independent_confirmation": False,
                }
            )
    return rows, matches


def decide(
    candidates: Sequence[NetworkCandidate],
    *,
    network: NetworkFacts,
    request: NetworkRequest,
    rules: Sequence[NetworkRule],
) -> NetworkDecision:
    """Decide over eligible nodes. Pure; see the module docstring for the semantics."""
    candidates = list(candidates)
    by_ref = {c.node_ref: c for c in candidates}
    if len(by_ref) != len(candidates):
        raise ValueError("candidate node refs must be distinct")
    registry = tuple(rules)
    validate_rules(registry)
    candidate_rules = tuple(r for r in registry if r.level == LEVEL_CANDIDATE) if request.apply_rules else ()
    rule_by_id = {r.rule_id: r for r in candidate_rules}

    edges: list[Edge] = []
    pair_checks: list[dict[str, Any]] = []
    rule_matches: list[dict[str, Any]] = []
    for rule in candidate_rules:
        header = {"rule_id": rule.rule_id, "rule_version": rule.version, "level": rule.level}
        not_applied = _applies_to_request(rule, request)
        if not_applied is not None:
            rule_matches.append(
                {**header, "applied": False, "why": not_applied, "reasons": list(rule.inactive_reasons)}
            )
            continue
        scope = rule.scope(network, request)
        if scope.state is not Tri.true:
            why = "scope_unknown" if scope.state is Tri.unknown else "outside_rule_scope"
            rule_matches.append({**header, "applied": False, "why": why, "scope": scope.to_dict()})
            continue
        sides = {c.node_ref: (_side(c, rule.preferred_side), _side(c, rule.yielding_side)) for c in candidates}
        for a in candidates:
            for b in candidates:
                if a.node_ref == b.node_ref:
                    continue
                if sides[a.node_ref][0].state is not Tri.true or sides[b.node_ref][1].state is not Tri.true:
                    continue
                compatible = _pair(rule, a, b)
                pair_checks.append(
                    {**header, "preferred": a.node_ref, "dispreferred": b.node_ref, "compatible": compatible.to_dict()}
                )
                if compatible.state is Tri.true:
                    edges.append(Edge(a.node_ref, b.node_ref, rule.rule_id, rule.version))
        rule_matches.append(
            {
                **header,
                "applied": True,
                "scope": scope.to_dict(),
                "candidates": [
                    {"node_ref": ref, "preferred": sides[ref][0].to_dict(), "yielding": sides[ref][1].to_dict()}
                    for ref in sorted(sides)
                ],
            }
        )

    def edge_record(edge: Edge) -> dict[str, Any]:
        rule = rule_by_id[edge.rule_id]
        return {
            **edge.to_dict(),
            "label": EDGE_LABEL,
            "objective": rule.objective.value,
            "objective_key": rule.objective_key,
        }

    objectives = sorted({rule_by_id[e.rule_id].objective_key for e in edges})
    nodes = [c.node for c in candidates]
    supersedes = {r.rule_id: r.supersedes for r in candidate_rules}
    unused: list[dict[str, Any]] = []
    if len(objectives) > 1:
        unused = [edge_record(e) for e in sorted(set(edges), key=lambda e: (e.preferred, e.dispreferred, e.rule_id))]
        graph_edges: list[Edge] = []
    else:
        graph_edges = edges
    verdict = decide_graph(nodes, graph_edges, supersedes=supersedes, admin_policy=request.admin_policy)
    basis = BASIS_OBJECTIVES_NOT_COMPOSED if unused else verdict.basis
    first = (
        {"node_ref": verdict.administrative_first_ref, "basis": ADMIN_FIRST_BASIS}
        if verdict.administrative_first_ref is not None
        else None
    )
    if verdict.outcome is Outcome.sole_eligible_candidate:
        selection_basis: str | None = SELECTION_BASIS_SOLE
    elif verdict.outcome is Outcome.policy_preferred:
        selection_basis = SELECTION_BASIS_SCIENTIFIC
    elif verdict.outcome is Outcome.incomparable_alternatives and request.first_only:
        selection_basis = SELECTION_BASIS_ADMIN_FIRST
    else:
        selection_basis = None
    representation_rows, representation_matches = _representations(candidates, network, request, registry)
    return NetworkDecision(
        outcome=verdict.outcome,
        selected_ref=verdict.selected_ref,
        selection_basis=selection_basis,
        basis=basis,
        fronts=verdict.fronts,
        administrative_order=verdict.administrative_order,
        administrative_first=first,
        edges=tuple(edge_record(e) for e in verdict.edges),
        overridden_edges=verdict.overridden_edges,
        opposing_pairs=verdict.opposing_pairs,
        cycles=verdict.cycles,
        unused_edges=tuple(unused),
        rule_matches=(*rule_matches, *representation_matches),
        pair_checks=tuple(pair_checks),
        rules=tuple(r.describe() for r in registry if request.apply_rules),
        rules_applied=request.apply_rules,
        representations=tuple(representation_rows),
    )


__all__ = [
    "BASIS_OBJECTIVES_NOT_COMPOSED",
    "BASIS_REPRESENTATIONS_ADMINISTRATIVE",
    "SELECTION_BASIS_ADMIN_FIRST",
    "SELECTION_BASIS_SCIENTIFIC",
    "SELECTION_BASIS_SOLE",
    "NetworkDecision",
    "build_candidates",
    "decide",
]
