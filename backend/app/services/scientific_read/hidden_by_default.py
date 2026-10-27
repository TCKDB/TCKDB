"""Disclosure of what the default visibility hid (exploratory profile only).

A list that hides rejected and deprecated records by default must say how many it hid, or a reader cannot tell "no
such record" from "hidden by default". This module computes that count and carries it to the response.

* :func:`hidden_by_default_block` is the count. It is **one aggregate statement** per list: the matched ids joined to
  their review rows, grouped by status. It makes no statement at all when nothing is excluded by default, and it
  returns ``None`` under the ``curated`` profile without querying: the record floor is the profile itself, not a default
  exclusion a request can lift, so there is no switch to point to and nothing to disclose (ADR 0023: a curated list
  returns
  approved records only).
* :func:`record_hidden_by_default` hands the block to the response boundary through a holder the profile dependency
  installs, the same way the resolved profile reaches it, so a scientific service does not thread it through its
  return value. :func:`stamp_hidden_by_default` writes it into the ``request`` echo at the one boundary every
  enveloped scientific response passes through.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.schemas.reads.scientific_common import HiddenByDefault, status_at_or_above
from app.services.scientific_read.profile import current_read_profile

#: The exclusions the default applies, with the request flag that lifts each.
DEFAULT_EXCLUSIONS: dict[RecordReviewStatus, str] = {
    RecordReviewStatus.rejected: "include_rejected=true",
    RecordReviewStatus.deprecated: "include_deprecated=true",
}


class HiddenByDefaultHolder:
    #: One block per scope. A search that is assembled from per-parent reads (one per species entry, per reaction
    #: entry) gets one block from each read; the response reports their sum. A read that is called again for the same
    #: scope (a second page) replaces its earlier block, so nothing is counted twice.
    blocks: dict[object, HiddenByDefault]

    def __init__(self) -> None:
        self.blocks = {}

    @property
    def value(self) -> HiddenByDefault | None:
        if not self.blocks:
            return None
        counted = {block.counted for block in self.blocks.values()}
        if len(counted) > 1:
            # Entries and records are different things to count: summing them would report a number of nothing.
            raise ValueError(f"cannot merge hidden-by-default blocks that count different things: {sorted(counted)}")
        counts: dict[str, int] = {}
        include_with: dict[str, str] = {}
        for block in self.blocks.values():
            for key, n in block.counts.items():
                counts[key] = counts.get(key, 0) + n
            include_with.update(block.include_with)
        return HiddenByDefault(counts=counts, include_with=include_with, counted=counted.pop())


_HOLDER: ContextVar[HiddenByDefaultHolder | None] = ContextVar("tckdb_hidden_by_default", default=None)


def set_holder() -> Token:
    return _HOLDER.set(HiddenByDefaultHolder())


def reset_holder(token: Token) -> None:
    _HOLDER.reset(token)


def record_hidden_by_default(block: HiddenByDefault | None, *, scope: object = None) -> None:
    """Remember the block for this request's response. A no-op outside a scientific request.

    ``scope`` names what the block counted (a parent id) when one response is assembled from several reads; blocks of
    different scopes are summed, a repeated scope replaces its earlier block.
    """
    holder = _HOLDER.get()
    if holder is None:
        return
    if block is None:
        holder.blocks.pop(scope, None)
    else:
        holder.blocks[scope] = block


def stamp_hidden_by_default(payload: object) -> None:
    """Write the recorded block into a response envelope's ``request`` echo (when that echo has the field)."""
    holder = _HOLDER.get()
    echo = getattr(payload, "request", None)
    if holder is None or echo is None or not hasattr(echo, "hidden_by_default"):
        return
    echo.hidden_by_default = holder.value


def excluded_by_default(
    *,
    include_rejected: bool,
    include_deprecated: bool,
    min_review_status: RecordReviewStatus | None = None,
) -> list[RecordReviewStatus]:
    """The statuses the default visibility excludes for this request (empty under ``curated``).

    A status a ``min_review_status`` threshold removes anyway is a filter's doing, not the default's, so it is not
    listed.
    """
    if current_read_profile().review_floor is not None:
        return []
    out = []
    for status in DEFAULT_EXCLUSIONS:
        lifted = include_rejected if status is RecordReviewStatus.rejected else include_deprecated
        if lifted:
            continue
        if min_review_status is not None and status not in status_at_or_above(min_review_status):
            continue
        out.append(status)
    return out


def hidden_by_default_block(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    ids: Any,
    include_rejected: bool,
    include_deprecated: bool,
    min_review_status: RecordReviewStatus | None = None,
    counted: str = "records",
) -> HiddenByDefault | None:
    """Count the matched records the default visibility excluded. One statement; none when nothing is excluded.

    ``ids`` is a ``select`` of one id column (the records matching every filter other than review visibility), or a
    list of ids. ``None`` under ``curated`` and when no exclusion is in force.
    """
    statuses = excluded_by_default(
        include_rejected=include_rejected, include_deprecated=include_deprecated, min_review_status=min_review_status
    )
    if not statuses:
        return None
    if isinstance(ids, (list, tuple, set, frozenset)):
        ids = list(ids)
        if not ids:
            return _block(statuses, {}, counted)
    rows = session.execute(
        select(RecordReview.status, func.count())
        .where(
            RecordReview.record_type == record_type,
            RecordReview.status.in_(statuses),
            RecordReview.record_id.in_(ids),
        )
        .group_by(RecordReview.status)
    ).all()
    return _block(statuses, dict(rows), counted)


def hidden_by_default_from_statuses(
    candidate_statuses: Any,
    *,
    include_rejected: bool,
    include_deprecated: bool,
    min_review_status: RecordReviewStatus | None = None,
) -> HiddenByDefault | None:
    """The same block, counted from review statuses the read already loaded (no statement).

    ``candidate_statuses`` is one status per candidate matching every filter other than review visibility.
    """
    statuses = excluded_by_default(
        include_rejected=include_rejected, include_deprecated=include_deprecated, min_review_status=min_review_status
    )
    if not statuses:
        return None
    found: dict = {}
    for status in candidate_statuses:
        if status in statuses:
            found[status] = found.get(status, 0) + 1
    return _block(statuses, found)


def hidden_by_default_from_counts(
    found: dict,
    *,
    include_rejected: bool,
    include_deprecated: bool,
    min_review_status: RecordReviewStatus | None = None,
    counted: str = "records",
) -> HiddenByDefault | None:
    """The block from per-status counts a read already aggregated (``found`` maps status to count)."""
    statuses = excluded_by_default(
        include_rejected=include_rejected, include_deprecated=include_deprecated, min_review_status=min_review_status
    )
    if not statuses:
        return None
    return _block(statuses, found, counted)


def _block(statuses: list[RecordReviewStatus], found: dict, counted: str = "records") -> HiddenByDefault:
    return HiddenByDefault(
        counted=counted,
        counts={s.value: int(found.get(s, 0)) for s in statuses},
        include_with={s.value: DEFAULT_EXCLUSIONS[s] for s in statuses},
    )


__all__ = [
    "DEFAULT_EXCLUSIONS",
    "excluded_by_default",
    "hidden_by_default_block",
    "hidden_by_default_from_counts",
    "hidden_by_default_from_statuses",
    "record_hidden_by_default",
    "reset_holder",
    "set_holder",
    "stamp_hidden_by_default",
]
