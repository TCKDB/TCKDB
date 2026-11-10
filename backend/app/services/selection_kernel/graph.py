"""The outcome over a set of eligible candidates. Pure: no database, no domain knowledge.

TCKDB does not rank one method against another: there is no rule, edge or preference between candidates. What a
selection can honestly say about the candidates that are eligible for the request is therefore only:

* none is eligible: ``no_applicable_candidate``;
* exactly one is: ``sole_eligible_candidate``, which says it is the only one, not that it is best;
* several are: ``incomparable_alternatives``. They form one front, listed in the administrative order the request
  names (review rank, then deposition time, then a unique tie-breaker, ``docs/specs/browse_order.md``). The first
  record is display order, never a scientific preference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import SelectionPolicy, simple_selection_sort_key
from app.services.selection_kernel.vocabulary import Outcome

BASIS_NONE_ELIGIBLE = "no candidate is eligible for this request"
BASIS_SOLE = "the only eligible candidate in the searched corpus; not a comparative or global-best claim"
BASIS_INCOMPARABLE = (
    "more than one candidate is eligible; TCKDB carries no ranking between methods, so none is preferred and they "
    "are listed in display order only"
)
ADMIN_FIRST_BASIS = (
    "first in display order among eligible alternatives; not a claim that the record is better, preferred or "
    "method-superior"
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


@dataclass(frozen=True)
class GraphVerdict:
    """The kernel's answer over one set of eligible nodes (public refs only).

    ``fronts`` holds one front, the eligible nodes in administrative order, or none when there is nothing eligible.
    ``administrative_first_ref`` is set only for ``incomparable_alternatives``; the caller labels it.
    """

    outcome: Outcome
    selected_ref: str | None
    basis: str
    fronts: tuple[tuple[str, ...], ...]
    administrative_order: tuple[str, ...]
    administrative_first_ref: str | None


def decide_alternatives(nodes: Sequence[AdminNode], *, admin_policy: SelectionPolicy) -> GraphVerdict:
    """The outcome over ``nodes``: none, a sole eligible candidate, or tied alternatives in administrative order."""
    nodes = list(nodes)
    if len({n.ref for n in nodes}) != len(nodes):
        raise ValueError("candidate refs must be distinct")
    order = tuple(n.ref for n in order_admin(nodes, admin_policy))
    if not nodes:
        return GraphVerdict(Outcome.no_applicable_candidate, None, BASIS_NONE_ELIGIBLE, (), order, None)
    if len(nodes) == 1:
        return GraphVerdict(Outcome.sole_eligible_candidate, order[0], BASIS_SOLE, (order,), order, None)
    return GraphVerdict(Outcome.incomparable_alternatives, None, BASIS_INCOMPARABLE, (order,), order, order[0])
