"""Recover the finding behind a curator task.

A curator task stores the identity of its finding (``finding_fingerprint``) and
a snapshot of the record-level summary, not the finding itself. The finding is
recovered here by projecting the submission's stored machine-review events
again and matching the fingerprint **exactly** -- the same
:func:`~app.services.machine_review.curator_tasks.compute_finding_fingerprint`
call the task builder made, with the same (absent) ``review_context``.

The match is limited to the source event when the task records one
(``source_audit_event`` match). A submission with several machine-review events
leaves ``source_audit_event_id`` null, and a rebuild overwrites it on open
tasks, so the fallback looks across all of the submission's machine-review
events (``submission_audit_events`` match). The fingerprint covers every
content field, so a task maps to exactly one finding; a rerun can repeat that
finding, which is why every event it appears in is listed. A task built with a
non-null ``review_context`` cannot be matched and reports ``not_found``: the
service never guesses.

Read-only. Costs a constant number of queries however many tasks and
submissions are asked about: one for the links and one for the events of all the
submissions involved.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import (
    CuratorTaskFindingMatch,
    SubmissionActorKind,
    SubmissionAuditEventKind,
)
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.submission import SubmissionAuditEvent, SubmissionRecordLink
from app.services.machine_review.curator_tasks import compute_finding_fingerprint
from app.services.machine_review.inspection import (
    SubmissionMachineReviewInspection,
    build_submission_machine_review_inspection,
)
from app.services.machine_review.schemas import MachineReviewFinding


@dataclass(frozen=True)
class CuratorTaskFindingResolution:
    """The finding behind one task and how it was found.

    ``audit_event_ids`` lists every machine-review event of the submission in
    which the finding occurs (sorted), empty for ``not_found``.
    """

    finding: MachineReviewFinding | None
    match: CuratorTaskFindingMatch
    audit_event_ids: tuple[int, ...] = ()


# (record_type, record_id) -> [(audit_event_id, fingerprint, finding)] newest event first.
_FindingIndex = dict[tuple[str, int], list[tuple[int, str, MachineReviewFinding]]]


def load_machine_review_inspections(
    session: Session, submission_ids: Iterable[int]
) -> dict[int, SubmissionMachineReviewInspection]:
    """Project each submission's stored machine-review events, in two queries."""
    ids = sorted(set(submission_ids))
    if not ids:
        return {}
    links: dict[int, list[SubmissionRecordLink]] = {i: [] for i in ids}
    for link in session.scalars(select(SubmissionRecordLink).where(SubmissionRecordLink.submission_id.in_(ids))):
        links[link.submission_id].append(link)
    events: dict[int, list[SubmissionAuditEvent]] = {i: [] for i in ids}
    for ev in session.scalars(
        select(SubmissionAuditEvent)
        .where(
            SubmissionAuditEvent.submission_id.in_(ids),
            SubmissionAuditEvent.event_kind == SubmissionAuditEventKind.llm_precheck_recorded,
            SubmissionAuditEvent.actor_kind == SubmissionActorKind.llm,
        )
        .order_by(SubmissionAuditEvent.id)
    ):
        events[ev.submission_id].append(ev)
    return {
        sid: build_submission_machine_review_inspection(
            submission_id=sid,
            submission_record_links=links[sid],
            submission_audit_events=events[sid],
        )
        for sid in ids
    }


def _index(inspection: SubmissionMachineReviewInspection) -> _FindingIndex:
    index: _FindingIndex = {}
    for record in inspection.record_inspections:
        if record.record_id is None:
            continue
        entries = index.setdefault((record.record_type, record.record_id), [])
        for review in record.all_record_reviews:
            if review.audit_event_id is None:
                continue
            for finding in review.findings:
                fingerprint = compute_finding_fingerprint(
                    finding=finding,
                    record_type=record.record_type,
                    record_id=record.record_id,
                    review_context=None,
                )
                entries.append((review.audit_event_id, fingerprint, finding))
        entries.sort(key=lambda e: e[0], reverse=True)
    return index


def resolve_curator_task_findings(
    session: Session, tasks: Sequence[MachineReviewCuratorTask]
) -> dict[int, CuratorTaskFindingResolution]:
    """Resolve each task's finding; see the module docstring for the rule."""
    inspections = load_machine_review_inspections(session, {t.submission_id for t in tasks})
    indexes = {sid: _index(inspection) for sid, inspection in inspections.items()}

    resolved: dict[int, CuratorTaskFindingResolution] = {}
    for task in tasks:
        entries = indexes.get(task.submission_id, {}).get((task.record_type.value, task.record_id), [])
        hits = [(event_id, finding) for event_id, fp, finding in entries if fp == task.finding_fingerprint]
        if not hits:
            resolved[task.id] = CuratorTaskFindingResolution(None, CuratorTaskFindingMatch.not_found)
            continue
        event_ids = tuple(sorted({event_id for event_id, _ in hits}))
        in_source = [h for h in hits if h[0] == task.source_audit_event_id]
        if in_source:
            resolved[task.id] = CuratorTaskFindingResolution(
                in_source[0][1], CuratorTaskFindingMatch.source_audit_event, event_ids
            )
        else:
            # ``hits`` is newest event first, so this is the latest occurrence.
            resolved[task.id] = CuratorTaskFindingResolution(
                hits[0][1], CuratorTaskFindingMatch.submission_audit_events, event_ids
            )
    return resolved


def curator_task_ids_by_fingerprint(
    session: Session, submission_id: int
) -> dict[tuple[str, int, str], int]:
    """``{(record_type, record_id, finding_fingerprint): task_id}`` for one submission."""
    rows = session.execute(
        select(
            MachineReviewCuratorTask.record_type,
            MachineReviewCuratorTask.record_id,
            MachineReviewCuratorTask.finding_fingerprint,
            MachineReviewCuratorTask.id,
        ).where(MachineReviewCuratorTask.submission_id == submission_id)
    )
    return {(rt.value, rid, fp): tid for rt, rid, fp, tid in rows}


def fingerprint_for_review_finding(finding: MachineReviewFinding, record_type: str, record_id: int) -> str:
    """The task-identity fingerprint of a finding the inspection shows."""
    return compute_finding_fingerprint(
        finding=finding, record_type=record_type, record_id=record_id, review_context=None
    )
