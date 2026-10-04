"""The versioned engineering bounds of a network selection, and the refusals that enforce them.

A bound is a resource limit, not a measure of confidence. Every count below is taken over the
*complete authorized population*, before any applicability filtering or grouping, so exceeding a
bound refuses the decision outright: there is never a selection from a prefix. A refusal names the
bound and the visible count (what the caller is entitled to know); it never names or counts rows
the read profile hides. Raising a limit is a documented revision of
:class:`~app.services.network_selection.models.SelectionBounds`, not a scientific rule change.
"""

from __future__ import annotations

from app.api.error_contract import CodedValueError
from app.services.network_selection.models import SelectionBounds

CODE_POPULATION_TOO_LARGE = "network_selection_population_too_large"
CODE_SNAPSHOT_TOO_LARGE = "network_selection_snapshot_too_large"

#: Bound name -> the :class:`SelectionBounds` field that holds its limit.
BOUND_FIELDS: dict[str, str] = {
    "solves": "solves",
    "kinetics_parents": "kinetics_parents",
    "channel_nodes": "channel_nodes",
    "bundle_nodes": "bundle_nodes",
    "states": "states",
    "channels": "channels",
    "required_outputs": "required_outputs",
    "evidence_entries": "evidence_entries",
    "numeric_cells": "numeric_cells",
}


def check_bound(bounds: SelectionBounds, name: str, count: int) -> None:
    """Refuse (422 ``network_selection_population_too_large``) when ``count`` exceeds the named bound.

    A count equal to the limit is allowed: the limit is the largest population that is decided.
    """
    limit = getattr(bounds, BOUND_FIELDS[name])
    if count > limit:
        raise CodedValueError(
            CODE_POPULATION_TOO_LARGE,
            f"the visible {name.replace('_', ' ')} number {count}, over the selection limit of {limit}; "
            "nothing was assessed and nothing was chosen from a subset.",
            context={"bound": name, "visible": count, "limit": limit, "bounds_version": bounds.version},
            message_prefix=False,
        )


def check_snapshot_size(bounds: SelectionBounds, size_bytes: int) -> None:
    """Refuse (422 ``network_selection_snapshot_too_large``) when the canonical public snapshot is too big."""
    if size_bytes > bounds.snapshot_bytes:
        raise CodedValueError(
            CODE_SNAPSHOT_TOO_LARGE,
            f"the canonical selection snapshot is {size_bytes} bytes, over the limit of {bounds.snapshot_bytes}; "
            "nothing was decided.",
            context={"size_bytes": size_bytes, "limit_bytes": bounds.snapshot_bytes, "bounds_version": bounds.version},
            message_prefix=False,
        )
