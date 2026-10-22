"""The thermo ordering engine: the eligible candidates in administrative order, through the shared kernel.

Input is a set of *eligible* candidates (physically applicable, above the review floor,
not rejected or deprecated) as :class:`NormalizedCandidate` values; output is a
:class:`Decision` over public refs. It reads no database, so a recorded decision replays
from its manifest alone.

TCKDB carries no method-versus-method ranking, so nothing here prefers one candidate to another: one eligible
candidate is a sole eligible candidate, several are incomparable alternatives listed in administrative order
(``docs/specs/browse_order.md``), none is no applicable candidate. Outcomes and ordering are the domain-neutral kernel
in :mod:`app.services.selection_kernel`, whose semantics are documented there.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import (  # noqa: F401  (the BASIS_* sentences are re-exported unchanged)
    ADMIN_FIRST_BASIS,
    BASIS_INCOMPARABLE,
    BASIS_NONE_ELIGIBLE,
    BASIS_SOLE,
    AdminNode,
    decide_alternatives,
    order_admin,
)
from app.services.thermo_selection.models import Decision, NormalizedCandidate


def _node(c: NormalizedCandidate) -> AdminNode:
    return AdminNode(ref=c.thermo_ref, id_rank=c.id_rank, review_status=c.review_status, created_at=c.created_at)


def decide(candidates: Sequence[NormalizedCandidate], *, admin_policy: SelectionPolicy) -> Decision:
    """Decide over eligible candidates. Pure; see the module docstring for the semantics."""
    candidates = list(candidates)
    if len({c.thermo_ref for c in candidates}) != len(candidates):
        raise ValueError("candidate refs must be distinct")
    verdict = decide_alternatives([_node(c) for c in candidates], admin_policy=admin_policy)
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
    )
