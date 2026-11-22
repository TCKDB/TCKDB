"""Per-record machine-review summary for a page of records.

For each ``(record_type, record_id)`` asked about: the latest machine-review
status over the record's own findings, and the curator tasks still open on it.
Built for the review queue, so it reads in bulk -- a constant number of queries
however many records are asked about:

1. the submission links of those records;
2. every link and every machine-review event of the submissions behind them
   (:func:`load_machine_review_inspections`);
3. the open curator tasks of those records.

Projection is the read-only inspection projection, one pass per submission with
links scoped to their own submission, so a finding never reaches a record its
submission does not link and a submission-wide finding never reaches any record
(the anti-fan-out rule). A record with no mapped review is ``not_run``. When a
record is linked from several submissions the newest review wins, by the read
model's rule.

Read-only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.db.models.common import MachineReviewCuratorTaskStateGroup, SubmissionRecordType
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.submission import SubmissionRecordLink
from app.services.machine_review.curator_task_findings import load_machine_review_inspections
from app.services.machine_review.read_model import (
    RecordMachineReview,
    build_machine_review_record_summary,
)
from app.services.machine_review.schemas import MachineReviewSeverity, MachineReviewStatus


@dataclass(frozen=True)
class RecordMachineFindings:
    """Latest machine-review state of one record and its open curator tasks."""

    status: MachineReviewStatus = MachineReviewStatus.not_run
    highest_severity: MachineReviewSeverity | None = None
    findings_count: int = 0
    reviewed_at: datetime | None = None
    open_task_count: int = 0
    open_task_ids: tuple[int, ...] = ()


def summarize_machine_findings_for_records(
    session: Session,
    keys: Iterable[tuple[SubmissionRecordType, int]],
) -> dict[tuple[SubmissionRecordType, int], RecordMachineFindings]:
    """Summaries for every key asked about (a record with no review is ``not_run``)."""
    wanted = sorted({(t, i) for t, i in keys}, key=lambda k: (k[0].value, k[1]))
    if not wanted:
        return {}

    submission_ids = set(
        session.scalars(
            select(SubmissionRecordLink.submission_id).where(
                tuple_(SubmissionRecordLink.record_type, SubmissionRecordLink.record_id).in_(wanted)
            )
        )
    )
    reviews: dict[tuple[str, str], list[RecordMachineReview]] = defaultdict(list)
    for inspection in load_machine_review_inspections(session, submission_ids).values():
        for record in inspection.record_inspections:
            reviews[(record.record_type, record.record_ref or "")].extend(record.all_record_reviews)

    open_states = MachineReviewCuratorTaskStateGroup.open.states()
    open_tasks: dict[tuple[SubmissionRecordType, int], list[int]] = defaultdict(list)
    for record_type, record_id, task_id in session.execute(
        select(
            MachineReviewCuratorTask.record_type,
            MachineReviewCuratorTask.record_id,
            MachineReviewCuratorTask.id,
        ).where(
            tuple_(MachineReviewCuratorTask.record_type, MachineReviewCuratorTask.record_id).in_(wanted),
            MachineReviewCuratorTask.workflow_state.in_(open_states),
        )
    ):
        open_tasks[(record_type, record_id)].append(task_id)

    result: dict[tuple[SubmissionRecordType, int], RecordMachineFindings] = {}
    for record_type, record_id in wanted:
        summary = build_machine_review_record_summary(
            record_type=record_type.value,
            record_ref=str(record_id),
            reviews=reviews.get((record_type.value, str(record_id)), ()),
        )
        task_ids = tuple(sorted(open_tasks.get((record_type, record_id), ())))
        result[(record_type, record_id)] = RecordMachineFindings(
            status=summary.status,
            highest_severity=summary.highest_severity,
            findings_count=summary.findings_count,
            reviewed_at=summary.reviewed_at,
            open_task_count=len(task_ids),
            open_task_ids=task_ids,
        )
    return result


__all__ = ["RecordMachineFindings", "summarize_machine_findings_for_records"]
