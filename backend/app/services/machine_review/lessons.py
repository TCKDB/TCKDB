"""Curator-written lessons for the machine reviewer: propose, edit, verify, retire, and load the verified ones.

Every function flushes and never commits; the caller owns the transaction. Only a ``verified`` lesson is ever
returned as a guidance entry. A lesson is written by a person: nothing on the worker or model path calls
:func:`propose_lesson`.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError, MachineReviewJobError, NotFoundError
from app.db.models.common import (
    MachineReviewCategory as DbMachineReviewCategory,
)
from app.db.models.common import (
    MachineReviewLessonSignal,
    MachineReviewLessonStatus,
    SubmissionRecordType,
)
from app.db.models.machine_review_job import MachineReviewJob
from app.db.models.machine_review_lesson import LESSON_TEXT_MAX_CHARS, MachineReviewLesson
from app.schemas.entities.machine_review_lesson import LessonScope
from app.services.machine_review.agent.guidance import GuidanceEntry
from app.services.machine_review.agent.tools import LESSON_KIND
from app.services.machine_review.guidance_corpus import known_check_codes

if TYPE_CHECKING:
    from app.db.models.machine_review_curator_task import MachineReviewCuratorTask

LESSON_TITLE = "Lesson written by a curator"
LESSON_AUTHOR = "curator"

#: Most verified lessons one job loads.
LESSON_LOAD_LIMIT = 200

NOT_FOUND_CODE = "machine_review_lesson_not_found"
RETIRED_CODE = "machine_review_lesson_retired"

_UNSET: Any = object()


class MachineReviewLessonError(MachineReviewJobError):
    """A refused lesson operation; the status and code ride on the exception."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _retired() -> MachineReviewLessonError:
    return MachineReviewLessonError("This lesson is retired.", status=409, code=RETIRED_CODE)


def _db_category(category: Any) -> DbMachineReviewCategory | None:
    """The stored enum for a category given as either vocabulary's member (they share values)."""
    return None if category is None or category is _UNSET else DbMachineReviewCategory(category.value)


def _clean_text(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > LESSON_TEXT_MAX_CHARS:
        raise DomainError(f"Lesson text must be 1 to {LESSON_TEXT_MAX_CHARS} characters.")
    return cleaned


def _check_scope(scope: LessonScope | None) -> None:
    code = scope.check_code if scope is not None else None
    if code is not None and code not in known_check_codes():
        raise MachineReviewLessonError(
            "scope.check_code is not a known warning or check code.", status=422, code="lesson_unknown_check_code"
        )


def get_lesson(session: Session, lesson_id: int) -> MachineReviewLesson:
    """One lesson, or the coded not-found."""
    lesson = session.get(MachineReviewLesson, lesson_id)
    if lesson is None:
        raise NotFoundError("Machine review lesson not found.", code=NOT_FOUND_CODE)
    return lesson


def propose_lesson(
    session: Session,
    *,
    author_id: int,
    text: str,
    signal: MachineReviewLessonSignal,
    category: Any,
    record_type: SubmissionRecordType | None = None,
    scope: LessonScope | None = None,
    source_job_id: int | None = None,
    source_curator_task_id: int | None = None,
) -> MachineReviewLesson:
    """Store a ``proposed`` lesson; it reaches no review until an admin verifies it."""
    if source_job_id is not None and session.get(MachineReviewJob, source_job_id) is None:
        raise NotFoundError("Machine review job not found.", code="machine_review_job_not_found")
    _check_scope(scope)
    lesson = MachineReviewLesson(
        text=_clean_text(text),
        signal=signal,
        category=_db_category(category),
        record_type=record_type,
        scope_json=scope.as_json() if scope is not None else None,
        status=MachineReviewLessonStatus.proposed,
        source_job_id=source_job_id,
        source_curator_task_id=source_curator_task_id,
        created_by=author_id,
    )
    session.add(lesson)
    session.flush()
    return lesson


def propose_lesson_from_task(
    session: Session, task: MachineReviewCuratorTask, text: str, author_id: int
) -> MachineReviewLesson:
    """A ``curator_dismissed_finding`` lesson for ``task``; the category is the recovered finding's, else none."""
    from app.services.machine_review.curator_task_findings import resolve_curator_task_findings

    finding = resolve_curator_task_findings(session, [task])[task.id].finding
    category = finding.category if finding is not None else None
    return propose_lesson(
        session,
        author_id=author_id,
        text=text,
        signal=MachineReviewLessonSignal.curator_dismissed_finding,
        category=category,
        record_type=task.record_type,
        source_curator_task_id=task.id,
    )


def edit_lesson(
    session: Session,
    lesson_id: int,
    *,
    actor_id: int,
    text: str = _UNSET,
    signal: MachineReviewLessonSignal = _UNSET,
    category: Any = _UNSET,
    record_type: SubmissionRecordType | None = _UNSET,
    scope: LessonScope | None = _UNSET,
) -> MachineReviewLesson:
    """Change the fields passed. A verified lesson stays verified, now stamped with the editor."""
    lesson = get_lesson(session, lesson_id)
    if lesson.status is MachineReviewLessonStatus.retired:
        raise _retired()
    if scope is not _UNSET:
        _check_scope(scope)
    if text is not _UNSET:
        lesson.text = _clean_text(text)
    if signal is not _UNSET:
        lesson.signal = signal
    if category is not _UNSET:
        lesson.category = _db_category(category)
    if record_type is not _UNSET:
        lesson.record_type = record_type
    if scope is not _UNSET:
        lesson.scope_json = scope.as_json() if scope is not None else None
    if lesson.status is MachineReviewLessonStatus.verified:
        lesson.verified_by, lesson.verified_at = actor_id, _now()
    session.flush()
    return lesson


def verify_lesson(session: Session, lesson_id: int, *, actor_id: int) -> MachineReviewLesson:
    """``proposed`` to ``verified``; verifying a verified lesson changes nothing, a retired one is refused."""
    lesson = get_lesson(session, lesson_id)
    if lesson.status is MachineReviewLessonStatus.retired:
        raise _retired()
    if lesson.status is MachineReviewLessonStatus.proposed:
        lesson.status = MachineReviewLessonStatus.verified
        lesson.verified_by, lesson.verified_at = actor_id, _now()
        session.flush()
    return lesson


def retire_lesson(session: Session, lesson_id: int, *, actor_id: int) -> MachineReviewLesson:
    """``proposed`` or ``verified`` to ``retired``; retiring a retired lesson changes nothing."""
    lesson = get_lesson(session, lesson_id)
    if lesson.status is not MachineReviewLessonStatus.retired:
        lesson.status = MachineReviewLessonStatus.retired
        lesson.retired_by, lesson.retired_at = actor_id, _now()
        session.flush()
    return lesson


def list_lessons(
    session: Session, *, status: MachineReviewLessonStatus | None = None, offset: int = 0, limit: int = 50
) -> tuple[list[MachineReviewLesson], int]:
    """Lessons newest first, with the total."""
    query = select(MachineReviewLesson)
    if status is not None:
        query = query.where(MachineReviewLesson.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(MachineReviewLesson.created_at.desc(), MachineReviewLesson.id.desc()).offset(offset).limit(limit)
    ).all()
    return list(rows), total


def _entry(lesson: MachineReviewLesson) -> GuidanceEntry:
    fields: dict[str, Any] = {"author": LESSON_AUTHOR, "signal": lesson.signal.value}
    if lesson.category is not None:
        fields["category"] = lesson.category.value
    if lesson.record_type is not None:
        fields["record_type"] = lesson.record_type.value
    scope = dict(lesson.scope_json or {})
    fields.update({k: v for k, v in scope.items() if k in ("software", "method_family")})
    check_code = scope.get("check_code")
    return GuidanceEntry(
        source_key=f"{LESSON_KIND}:{lesson.id}",
        kind=LESSON_KIND,
        title=LESSON_TITLE,
        text=lesson.text,
        fields=fields,
        codes=(check_code,) if check_code else (),
    )


def verified_lesson_entries(
    session: Session, *, record_types: Collection[str], limit: int = LESSON_LOAD_LIMIT
) -> list[GuidanceEntry]:
    """Guidance entries for the newest verified lessons that apply to ``record_types`` or to every record type."""
    types = []
    for value in record_types:
        try:
            types.append(SubmissionRecordType(value))
        except ValueError:
            continue
    applies = MachineReviewLesson.record_type.is_(None)
    if types:
        applies = or_(applies, MachineReviewLesson.record_type.in_(types))
    rows = session.scalars(
        select(MachineReviewLesson)
        .where(MachineReviewLesson.status == MachineReviewLessonStatus.verified, applies)
        .order_by(MachineReviewLesson.verified_at.desc(), MachineReviewLesson.id.desc())
        .limit(limit)
    )
    return [_entry(lesson) for lesson in rows]


__all__ = [
    "LESSON_KIND",
    "LESSON_LOAD_LIMIT",
    "MachineReviewLessonError",
    "edit_lesson",
    "get_lesson",
    "list_lessons",
    "propose_lesson",
    "propose_lesson_from_task",
    "retire_lesson",
    "verified_lesson_entries",
    "verify_lesson",
]
