"""The "made under" stamp of the stored machine review a curator task came from, read against the registry.

A stored machine review names the prompt and the rubric it was made under (``prompt_name``/``prompt_version``,
``rubric_name``/``rubric_version``). This reads those stamps for a page of tasks in one grouped query and says whether
they are still the ones in force, **using the currency rule** (:func:`app.services.machine_review.currency.stamp_mismatches`),
not a second comparison: a different name or version of the prompt or the rubric, or a stored version 0 (stamped before
the 2026-10 versioning reset), makes the review ``stale``.

A task reads the review that produced it: the row whose ``source_audit_event_id`` is the task's. A task without a source
event reads the record's latest reviewer-family review. A task whose source review is not stored has no entry.

The evidence the review saw is not re-hashed here, so a review whose stamps are current is ``inputs_unchecked``, never
``current``: the full currency rule also compares the evidence, and that is not done for a page of tasks.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.schemas.reads.scientific_assessment import MadeUnder
from app.services.machine_review.currency import MachineReviewStaleReason, stamp_mismatches
from app.services.machine_review.query import SCIENTIFIC_CHECK_PROVIDER, _latest_first_order_by
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_PROMPT
from app.services.trust.rubrics import RUBRICS
from app.versioning import StoredVersionRef, UnknownVersion


def _made_under(row: RecordMachineReviewRow) -> MadeUnder:
    try:
        active_rubric = RUBRICS.current(row.rubric_name)
    except UnknownVersion:
        active_rubric = None
    if active_rubric is None:
        reasons: tuple[MachineReviewStaleReason, ...] = (MachineReviewStaleReason.rubric_versions_mismatch,)
    else:
        reasons = stamp_mismatches(
            StoredVersionRef(row.prompt_name, row.prompt_version),
            StoredVersionRef(row.rubric_name, row.rubric_version),
            ACTIVE_MACHINE_REVIEW_PROMPT,
            active_rubric,
        )
    if not reasons:
        state, reason = "inputs_unchecked", None
    elif MachineReviewStaleReason.rubric_versions_mismatch in reasons:
        state, reason = "stale", "rubric_version"
    else:
        state, reason = "stale", "prompt_version"
    return MadeUnder(
        rubric=row.rubric_name,
        assessed_version=row.rubric_version,
        current_rubric=active_rubric.name if active_rubric is not None else None,
        current_version=active_rubric.version if active_rubric is not None else None,
        state=state,
        stale_reason=reason,
        inputs_checked=False,
    )


def machine_review_made_under(session: Session, tasks: Iterable[MachineReviewCuratorTask]) -> dict[int, MadeUnder]:
    """``{task id: MadeUnder}`` for the tasks whose source review is stored."""
    wanted = list(tasks)
    if not wanted:
        return {}
    keys = list({(t.record_type, t.record_id) for t in wanted})
    rows = session.scalars(
        select(RecordMachineReviewRow)
        .where(
            tuple_(RecordMachineReviewRow.record_type, RecordMachineReviewRow.record_id).in_(keys),
            or_(
                RecordMachineReviewRow.provider.is_(None),
                RecordMachineReviewRow.provider != SCIENTIFIC_CHECK_PROVIDER,
            ),
        )
        .order_by(*_latest_first_order_by())
    ).all()  # newest first
    by_record: dict[tuple[object, int], list[RecordMachineReviewRow]] = {}
    for row in rows:
        by_record.setdefault((row.record_type, row.record_id), []).append(row)
    out: dict[int, MadeUnder] = {}
    for task in wanted:
        candidates = by_record.get((task.record_type, task.record_id), [])
        if task.source_audit_event_id is None:
            chosen = candidates[0] if candidates else None
        else:
            chosen = next((r for r in candidates if r.source_audit_event_id == task.source_audit_event_id), None)
        if chosen is not None:
            out[task.id] = _made_under(chosen)
    return out
