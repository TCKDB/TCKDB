"""Read-side resolution of accepted-science supersession chains.

Why this module exists
---------------------
A correction to an accepted scientific record never rewrites the record. It
appends a new record plus one immutable ``scientific_record_supersession``
edge. The old row stays byte-identical so an existing citation keeps
resolving.

That guarantee is only half a contract. A citation that 404s announces its
own problem; a citation that resolves cleanly to a *superseded* number looks
healthy, so nobody investigates. Findable-and-unmarked is the exact failure
the append-only design exists to prevent, and until this module existed no
scientific product read told a reader that the number they had just been
handed had been replaced.

Store the chain, compute the head
---------------------------------
The table stores only ``superseded → superseding``, one edge per correction.
A read of a superseded record needs two different answers:

* its **immediate** successor — truthful about the single recorded edge, and
  what preserves the history;
* the **head** of the chain — what a reader actually wants to follow.

The head is deliberately *not* stored. Storing it would mean ``UPDATE``-ing
every earlier record in the chain whenever a new correction lands, which the
accepted-science immutability triggers refuse (ADR 0003) and which is the
same second-source-of-truth defect ADR 0007 rejected as a stored
``is_current`` flag. Appending a correction stays one ``INSERT``; every read
of every earlier record reports the new head immediately, because the head
was never written down.

Cost
----
Two queries per page, independent of page size:

1. one recursive CTE that walks every requested id to the end of its chain;
2. one ``SELECT id, public_ref`` over the root table for the successor and
   head ids the walk found.

Resolving a chain per row would be the obvious N+1 trap, so this mirrors
:func:`app.services.scientific_read.common.fetch_review_badges`: hand it the
whole page's ids at once. It inherits that function's constraint too — one
bind parameter per requested id, so callers must pass a page, not an
unbounded candidate set (see the module docstring of ``sql_review.py``).

Linearity
---------
``UNIQUE (record_type, superseded_record_id)`` means a record can be
superseded at most once, so a chain cannot fork and "the head" is
well-defined. ``UNIQUE (record_type, superseding_record_id)`` means it cannot
merge either. The write service additionally refuses cycles. The recursion
still carries a depth cap: a cycle introduced by any future path would
otherwise turn a read into an infinite walk, and a read is the wrong place to
discover that.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import literal, select
from sqlalchemy.orm import Session, aliased

from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType
from app.db.models.kinetics import Kinetics
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkSolve
from app.db.models.scientific_record_supersession import ScientificRecordSupersession
from app.db.models.species import ConformerObservation
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from app.db.models.transport import Transport
from app.schemas.reads.scientific_common import SupersessionNotice

#: Root models whose rows can be *named* in a notice. Every entry carries
#: ``PublicRefMixin``, so a notice can cite ``public_ref`` and never a
#: database row id (DR-0028 Req 2).
#:
#: ``applied_energy_correction`` is a supported supersession *type* but is
#: absent here on purpose: ``AppliedEnergyCorrection`` has no ``public_ref``
#: column, so there is no way to point at its replacement without leaking a
#: primary key. Giving that table a public ref is the prerequisite for its
#: notice, not something this module may work around.
_REF_BEARING_ROOTS: dict[SubmissionRecordType, type[Any]] = {
    SubmissionRecordType.calculation: Calculation,
    SubmissionRecordType.thermo: Thermo,
    SubmissionRecordType.statmech: Statmech,
    SubmissionRecordType.kinetics: Kinetics,
    SubmissionRecordType.transport: Transport,
    SubmissionRecordType.network: Network,
    SubmissionRecordType.network_solve: NetworkSolve,
    SubmissionRecordType.transition_state_entry: TransitionStateEntry,
    SubmissionRecordType.conformer_observation: ConformerObservation,
}

#: Hard ceiling on recursion depth. Far above any plausible correction chain;
#: present only so a malformed chain degrades into a truncated notice rather
#: than an unbounded query.
_MAX_CHAIN_DEPTH = 64


def supports_supersession_notices(record_type: SubmissionRecordType) -> bool:
    """Return whether a notice can be produced for ``record_type``."""

    return record_type in _REF_BEARING_ROOTS


def fetch_supersession_notices(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_ids: Iterable[int],
) -> dict[int, SupersessionNotice]:
    """Bulk-resolve correction notices for ``(record_type, record_id)`` pairs.

    Returns a mapping from record_id → :class:`SupersessionNotice`, containing
    an entry **only** for ids that have actually been superseded. A current
    record is simply absent from the mapping, so callers should use
    ``notices.get(record_id)`` and attach ``None`` — a current record must
    report nothing at all rather than an empty block, which a client would
    have to inspect to distinguish from a real notice.
    """

    ids = sorted({int(record_id) for record_id in record_ids})
    if not ids:
        return {}
    root = _REF_BEARING_ROOTS.get(record_type)
    if root is None:
        return {}

    edge = ScientificRecordSupersession
    # Seed: the one edge leaving each requested record. Its own fields
    # (successor, reason, timestamp) are carried unchanged through the
    # recursion, because the notice describes the *immediate* edge even while
    # the walk continues to the head.
    walk = (
        select(
            edge.superseded_record_id.label("origin_id"),
            edge.superseding_record_id.label("cursor_id"),
            edge.superseding_record_id.label("successor_id"),
            edge.reason.label("reason"),
            edge.created_at.label("superseded_at"),
            literal(1).label("depth"),
        )
        .where(
            edge.record_type == record_type,
            edge.superseded_record_id.in_(ids),
        )
        .cte("supersession_walk", recursive=True)
    )
    onward = aliased(edge)
    walk = walk.union_all(
        select(
            walk.c.origin_id,
            onward.superseding_record_id,
            walk.c.successor_id,
            walk.c.reason,
            walk.c.superseded_at,
            walk.c.depth + 1,
        ).where(
            onward.record_type == record_type,
            onward.superseded_record_id == walk.c.cursor_id,
            walk.c.depth < _MAX_CHAIN_DEPTH,
        )
    )
    rows = session.execute(
        select(
            walk.c.origin_id,
            walk.c.cursor_id,
            walk.c.successor_id,
            walk.c.reason,
            walk.c.superseded_at,
            walk.c.depth,
        ).order_by(walk.c.origin_id, walk.c.depth)
    ).all()
    if not rows:
        return {}

    # Rows arrive ordered by depth, so the last row for an origin is the
    # deepest reachable record: the head. The seed row (depth 1) supplies the
    # immediate successor and the edge's own reason/timestamp.
    heads: dict[int, tuple[int, int]] = {}
    immediate: dict[int, tuple[int, str, Any]] = {}
    for origin_id, cursor_id, successor_id, reason, superseded_at, depth in rows:
        heads[origin_id] = (cursor_id, depth)
        if depth == 1:
            immediate[origin_id] = (successor_id, reason, superseded_at)

    wanted = {head_id for head_id, _ in heads.values()}
    wanted.update(successor_id for successor_id, _, _ in immediate.values())
    refs = dict(
        session.execute(
            select(root.id, root.public_ref).where(root.id.in_(sorted(wanted)))
        ).all()
    )

    notices: dict[int, SupersessionNotice] = {}
    for origin_id, (successor_id, reason, superseded_at) in immediate.items():
        head_id, chain_length = heads[origin_id]
        successor_ref = refs.get(successor_id)
        head_ref = refs.get(head_id)
        if successor_ref is None or head_ref is None:
            # A referenced record has vanished. Emitting a partial notice
            # would mean either a null pointer a client cannot follow or a
            # raw row id in a public body; silence is the honest answer, and
            # the record's ``deprecated`` review badge still stands.
            continue
        notices[origin_id] = SupersessionNotice(
            superseded_by=successor_ref,
            current=head_ref,
            reason=reason,
            superseded_at=superseded_at,
            chain_length=chain_length,
        )
    return notices


__all__ = [
    "fetch_supersession_notices",
    "supports_supersession_notices",
]
