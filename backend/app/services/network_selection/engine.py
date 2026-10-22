"""The network ordering engine: eligible decision nodes in administrative order, through the shared kernel.

Input is the *eligible* decision nodes (a determination with all of its eligible fits, or a declared product set
with its members) and the request; output is a :class:`NetworkDecision` over public refs. It reads no database, so
a recorded decision replays from its manifest alone. Outcomes and ordering are the kernel's
(:mod:`app.services.selection_kernel`), exactly as for H298 and elementary kinetics; nothing here changes them.

What is specific to networks, and lives here and not in the kernel:

* **The competition unit is a determination (one channel) or a declared product set (a bundle).** Nothing ranks one
  node above another: TCKDB carries no method-versus-method ranking, so one eligible node is a sole eligible
  candidate, several are incomparable alternatives listed in administrative order (``docs/specs/browse_order.md``),
  none is no applicable candidate. There is no averaging, no weighting, no stitching of temperature windows and no
  splicing of channels across solves.
* **Alternate fits are one candidate.** Within a determination the eligible fits are listed in administrative order,
  said so: that order is not a claim that one fit is more accurate, and the fits add no independent confirmation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.services.network_selection.models import (
    BundleAssessment,
    DeterminationAssessment,
    FitFacts,
    NetworkRequest,
    Scope,
    SolveFacts,
)
from app.services.selection_kernel import ADMIN_FIRST_BASIS, AdminNode, Outcome, decide_alternatives

BASIS_REPRESENTATIONS_ADMINISTRATIVE = (
    "the fits of one determination are listed in display order, which is not a claim that one fit is more accurate "
    "and adds no independent confirmation"
)

SELECTION_BASIS_SOLE = "sole_eligible_candidate"
SELECTION_BASIS_ADMIN_FIRST = "administrative_first"


@dataclass(frozen=True)
class MemberFacts:
    """One determination of a candidate: its channel, observable and the eligible fits that represent it."""

    determination_ref: str
    channel_key: str
    observable: dict[str, Any]
    fits: tuple[FitFacts, ...]


@dataclass(frozen=True)
class NetworkCandidate:
    """One eligible decision node, its generating solve and its members.

    A channel-scope node has one member (the determination); a bundle node has one member per required
    output answered by a determination. ``node`` is the kernel's administrative view of it.
    """

    node_ref: str
    scope: str
    solve: SolveFacts
    members: tuple[MemberFacts, ...]
    node: AdminNode


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

    ``selected_ref`` is set only for ``sole_eligible_candidate``. For ``incomparable_alternatives`` the one front is
    in ``fronts`` and the administrative first, labelled as such, in ``administrative_first``: ``result_mode=first``
    may name it, and the outcome stays incomparable.
    """

    outcome: Outcome
    selected_ref: str | None
    selection_basis: str | None
    basis: str
    fronts: tuple[tuple[str, ...], ...]
    administrative_order: tuple[str, ...]
    administrative_first: dict[str, str] | None
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
            "representations": [dict(r) for r in self.representations],
        }


def _representations(candidates: Sequence[NetworkCandidate], request: NetworkRequest) -> list[dict[str, Any]]:
    """The fits inside each member of each eligible node, in administrative order."""
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        for member in candidate.members:
            nodes = [
                AdminNode(f.fit_ref, f.id_rank, candidate.solve.review_status, candidate.solve.created_at) for f in member.fits
            ]
            verdict = decide_alternatives(nodes, admin_policy=request.admin_policy)
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
                    "basis": BASIS_REPRESENTATIONS_ADMINISTRATIVE,
                    "shared_provenance": {"solve_ref": candidate.solve.solve_ref},
                    "independent_confirmation": False,
                }
            )
    return rows


def decide(candidates: Sequence[NetworkCandidate], *, request: NetworkRequest) -> NetworkDecision:
    """Decide over eligible nodes. Pure; see the module docstring for the semantics."""
    candidates = list(candidates)
    if len({c.node_ref for c in candidates}) != len(candidates):
        raise ValueError("candidate node refs must be distinct")
    verdict = decide_alternatives([c.node for c in candidates], admin_policy=request.admin_policy)
    first = (
        {"node_ref": verdict.administrative_first_ref, "basis": ADMIN_FIRST_BASIS}
        if verdict.administrative_first_ref is not None
        else None
    )
    if verdict.outcome is Outcome.sole_eligible_candidate:
        selection_basis: str | None = SELECTION_BASIS_SOLE
    elif verdict.outcome is Outcome.incomparable_alternatives and request.first_only:
        selection_basis = SELECTION_BASIS_ADMIN_FIRST
    else:
        selection_basis = None
    return NetworkDecision(
        outcome=verdict.outcome,
        selected_ref=verdict.selected_ref,
        selection_basis=selection_basis,
        basis=verdict.basis,
        fronts=verdict.fronts,
        administrative_order=verdict.administrative_order,
        administrative_first=first,
        representations=tuple(_representations(candidates, request)),
    )


__all__ = [
    "BASIS_REPRESENTATIONS_ADMINISTRATIVE",
    "SELECTION_BASIS_ADMIN_FIRST",
    "SELECTION_BASIS_SOLE",
    "MemberFacts",
    "NetworkCandidate",
    "NetworkDecision",
    "build_candidates",
    "decide",
]
