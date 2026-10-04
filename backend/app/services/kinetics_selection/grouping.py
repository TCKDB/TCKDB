"""Group the eligible representations of one determination into one candidate.

Several fitted representations (a Troe fit and a PLOG fit of one master-equation result, two fits
of one measurement) answer the same physical question once. Counting them separately would turn
alternate fits into independent confirmation, so a comparison is made between *determinations*,
each carried by its representations together.

Only eligible records are grouped, and only a record with a declared determination can be
eligible: a legacy record without one is ``unresolved`` upstream, never given a synthetic
determination. The representations of a group are in administrative order (the requested policy's
sort over review status, recency and id order), which is stable display ordering and no claim
that one fit is more accurate; the first is the group's representative.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.kinetics_selection.models import DeterminationGroup, NormalizedKinetics
from app.services.selection_kernel import AdminNode, order_admin


def admin_node(c: NormalizedKinetics) -> AdminNode:
    return AdminNode(ref=c.kinetics_ref, id_rank=c.id_rank, review_status=c.review_status, created_at=c.created_at)


def group_by_determination(
    eligible: Sequence[NormalizedKinetics], *, admin_policy: SelectionPolicy
) -> tuple[DeterminationGroup, ...]:
    """Eligible records grouped by determination, groups in administrative order of their representatives."""
    by_ref = {c.kinetics_ref: c for c in eligible}
    if len(by_ref) != len(eligible):
        raise ValueError("candidate refs must be distinct")
    members: dict[str, list[NormalizedKinetics]] = {}
    for c in eligible:
        if c.determination is None:
            raise ValueError(f"{c.kinetics_ref} has no declared determination and cannot be eligible")
        members.setdefault(c.determination.determination_ref, []).append(c)
    groups: list[DeterminationGroup] = []
    for determination_ref, records in members.items():
        ordered = [by_ref[n.ref] for n in order_admin([admin_node(c) for c in records], admin_policy)]
        head = ordered[0]
        groups.append(
            DeterminationGroup(
                determination_ref=determination_ref,
                representation_refs=tuple(c.kinetics_ref for c in ordered),
                representative_ref=head.kinetics_ref,
                representative_id_rank=head.id_rank,
                representative_review_status=head.review_status,
                representative_created_at=head.created_at,
            )
        )
    nodes = {
        g.determination_ref: AdminNode(
            ref=g.determination_ref,
            id_rank=g.representative_id_rank,
            review_status=g.representative_review_status,
            created_at=g.representative_created_at,
        )
        for g in groups
    }
    by_determination = {g.determination_ref: g for g in groups}
    return tuple(by_determination[n.ref] for n in order_admin(list(nodes.values()), admin_policy))


def group_nodes(groups: Sequence[DeterminationGroup]) -> list[AdminNode]:
    """The kernel nodes of the groups: one per determination, keyed by the representative's own sort fields."""
    return [
        AdminNode(
            ref=g.determination_ref,
            id_rank=g.representative_id_rank,
            review_status=g.representative_review_status,
            created_at=g.representative_created_at,
        )
        for g in groups
    ]
