"""The "made under" stamp of the latest stored machine review of each record, read against the rubric registry.

A stored machine review names the rubric it was made under (``rubric_name`` and ``rubric_version``). This reads those
stamps for a page of records in one grouped query and says, for each, whether that is still the version in force:
``current`` when the registry's current version of that rubric is the stored one, ``stale`` otherwise (a version that
is no longer current, a stamp from before the 2026-10 versioning reset, which is 0 and never current, or a rubric the
registry no longer holds). It compares the stamp only, not the evidence the review saw; the full currency rule (which
also compares the evidence hash and the prompt) is :mod:`app.services.machine_review.currency`.

A record with no stored reviewer-family review has no entry: there is nothing to say it was made under.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from app.db.models.common import SubmissionRecordType
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.schemas.reads.scientific_assessment import MadeUnder
from app.services.machine_review.query import SCIENTIFIC_CHECK_PROVIDER, _latest_first_order_by
from app.services.trust.rubrics import RUBRICS
from app.versioning import UnknownVersion


def latest_machine_review_made_under(
    session: Session, keys: Iterable[tuple[SubmissionRecordType, int]]
) -> dict[tuple[SubmissionRecordType, int], MadeUnder]:
    """``{(record_type, record_id): MadeUnder}`` for the records that have a stored reviewer-family review."""
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        return {}
    rows = session.scalars(
        select(RecordMachineReviewRow)
        .where(
            tuple_(RecordMachineReviewRow.record_type, RecordMachineReviewRow.record_id).in_(wanted),
            or_(
                RecordMachineReviewRow.provider.is_(None),
                RecordMachineReviewRow.provider != SCIENTIFIC_CHECK_PROVIDER,
            ),
        )
        .order_by(*_latest_first_order_by())
    ).all()
    out: dict[tuple[SubmissionRecordType, int], MadeUnder] = {}
    for row in rows:  # newest first: the first row seen for a record is its latest
        key = (row.record_type, row.record_id)
        if key in out:
            continue
        try:
            current = RUBRICS.current(row.rubric_name).version
        except UnknownVersion:
            current = None
        is_current = current is not None and current == row.rubric_version
        out[key] = MadeUnder(
            rubric=row.rubric_name,
            assessed_version=row.rubric_version,
            current_rubric=row.rubric_name if current is not None else None,
            current_version=current,
            state="current" if is_current else "stale",
            stale_reason=None if is_current else "rubric_version",
            inputs_checked=False,
        )
    return out
