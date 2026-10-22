"""The kinetics ordering engine: eligible determinations in administrative order, through the shared kernel.

Input is the *eligible* determinations (each with all of its eligible representations) and the request;
output is a :class:`KineticsDecision` over public refs. It reads no database, so a recorded decision replays
from its manifest alone.

What is specific to kinetics, and lives here and not in the kernel:

* **The competition unit is a determination.** Several eligible fitted representations of one determination are
  alternate fits of one candidate, never independent confirmation, and the first is not scientifically superior.
* **Nothing ranks one determination above another.** TCKDB carries no method-versus-method ranking: one eligible
  determination is a sole eligible candidate, several are incomparable alternatives listed in administrative order
  (``docs/specs/browse_order.md``), none is no applicable candidate.

Outcomes and ordering are the kernel's (:mod:`app.services.selection_kernel`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.kinetics_selection.grouping import group_by_determination, group_nodes
from app.services.kinetics_selection.models import NormalizedKinetics
from app.services.selection_kernel import ADMIN_FIRST_BASIS, AdminNode, Outcome, decide_alternatives


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "selected_determination_ref": self.selected_determination_ref,
            "representation_refs": list(self.representation_refs),
            "basis": self.basis,
            "fronts": [list(f) for f in self.fronts],
            "administrative_order": list(self.administrative_order),
            "administrative_first": self.administrative_first,
        }


def decide(candidates: Sequence[DeterminationCandidate], *, admin_policy: SelectionPolicy) -> KineticsDecision:
    """Decide over eligible determinations. Pure; see the module docstring for the semantics."""
    candidates = list(candidates)
    by_ref = {c.determination_ref: c for c in candidates}
    if len(by_ref) != len(candidates):
        raise ValueError("determination refs must be distinct")
    verdict = decide_alternatives([c.node for c in candidates], admin_policy=admin_policy)
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
        basis=verdict.basis,
        fronts=verdict.fronts,
        administrative_order=verdict.administrative_order,
        administrative_first=first,
    )


__all__ = [
    "DeterminationCandidate",
    "KineticsDecision",
    "build_candidates",
    "decide",
]
