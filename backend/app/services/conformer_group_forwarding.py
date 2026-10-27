"""Follow a merged conformer group to the group that took its members.

A merge empties the younger group and keeps its row, so a ``cg_`` ref cited before the merge still resolves, to a
group with no members. A READ of that ref says where it went (``merged_into_conformer_group_ref``). A WRITE that
cites it -- a thermo target, a statmech basin declaration or ensemble member, a kinetics conformer-selection locator --
must not attach to the empty row: it is forwarded to the group that now holds the basin, the same way a write that
names a merged level of theory lands on the level it was merged into.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.species import ConformerGroup

#: Merges chain only when the group that took the members was itself later merged into an older one; this bounds the
#: walk against a cycle that no code writes but a hand edit could.
_MAX_HOPS = 16


def forwarded_group_id(session: Session, group_id: int) -> int:
    """``group_id``, or the group it was merged into (following the chain), if it was emptied by a merge."""
    seen = {group_id}
    current = group_id
    for _ in range(_MAX_HOPS):
        target = session.scalar(
            select(ConformerGroup.merged_into_conformer_group_id).where(ConformerGroup.id == current)
        )
        if target is None or target in seen:
            return current
        seen.add(target)
        current = target
    return current
