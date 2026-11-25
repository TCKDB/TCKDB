"""Write, list, edit, verify and retire the curator-written lessons the machine reviewer may search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import get_db, get_write_db, require_admin, require_curator_or_admin
from app.api.routes._pagination import PaginatedResponse
from app.api.server_faults import ServerFaultRoute
from app.db.models.app_user import AppUser
from app.db.models.common import MachineReviewLessonStatus
from app.db.models.machine_review_lesson import MachineReviewLesson
from app.schemas.entities.machine_review_lesson import (
    MachineReviewLessonCreateRequest,
    MachineReviewLessonRead,
    MachineReviewLessonUpdate,
)
from app.services.machine_review import lessons

router = APIRouter(route_class=ServerFaultRoute)
admin_router = APIRouter(route_class=ServerFaultRoute)

_LESSON_ID = Path(ge=1, le=2**63 - 1)


def _reads(session: Session, rows: list[MachineReviewLesson]) -> list[MachineReviewLessonRead]:
    ids = {row.created_by for row in rows}
    names = dict(session.execute(select(AppUser.id, AppUser.username).where(AppUser.id.in_(ids))).all()) if ids else {}
    out = []
    for row in rows:
        item = MachineReviewLessonRead.model_validate(row)
        item.created_by_username = names.get(row.created_by)
        out.append(item)
    return out


@router.post(
    "/lessons",
    response_model=MachineReviewLessonRead,
    status_code=201,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def create_lesson(
    body: MachineReviewLessonCreateRequest,
    session: Session = Depends(get_write_db, scope="function"),
    user: AppUser = Depends(require_curator_or_admin),
) -> MachineReviewLessonRead:
    """Propose a lesson for the machine reviewer (curator/admin). It stays inert until an admin verifies it."""
    lesson = lessons.propose_lesson(
        session,
        author_id=user.id,
        text=body.text,
        signal=body.signal,
        category=body.category,
        record_type=body.record_type,
        scope=body.scope,
        source_job_id=body.source_job_id,
    )
    return _reads(session, [lesson])[0]


@admin_router.get("/lessons", response_model=PaginatedResponse[MachineReviewLessonRead])
def list_lessons(
    status: MachineReviewLessonStatus | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_db),
    _admin: AppUser = Depends(require_admin),
) -> PaginatedResponse[MachineReviewLessonRead]:
    """Lessons newest first, optionally of one status (admin only)."""
    rows, total = lessons.list_lessons(session, status=status, offset=offset, limit=limit)
    return PaginatedResponse(items=_reads(session, rows), total=total, skip=offset, limit=limit)


@admin_router.patch("/lessons/{lesson_id}", response_model=MachineReviewLessonRead)
def edit_lesson(
    body: MachineReviewLessonUpdate,
    lesson_id: int = _LESSON_ID,
    session: Session = Depends(get_write_db, scope="function"),
    admin: AppUser = Depends(require_admin),
) -> MachineReviewLessonRead:
    """Change the fields that are sent on a proposed or verified lesson (admin only); 409 once retired."""
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    lesson = lessons.edit_lesson(session, lesson_id, actor_id=admin.id, **changes)
    return _reads(session, [lesson])[0]


@admin_router.post("/lessons/{lesson_id}/verify", response_model=MachineReviewLessonRead)
def verify_lesson(
    lesson_id: int = _LESSON_ID,
    session: Session = Depends(get_write_db, scope="function"),
    admin: AppUser = Depends(require_admin),
) -> MachineReviewLessonRead:
    """Make a proposed lesson searchable by the machine reviewer (admin only); 409 once retired."""
    return _reads(session, [lessons.verify_lesson(session, lesson_id, actor_id=admin.id)])[0]


@admin_router.post("/lessons/{lesson_id}/retire", response_model=MachineReviewLessonRead)
def retire_lesson(
    lesson_id: int = _LESSON_ID,
    session: Session = Depends(get_write_db, scope="function"),
    admin: AppUser = Depends(require_admin),
) -> MachineReviewLessonRead:
    """Take a lesson out of the machine reviewer's reach for good (admin only)."""
    return _reads(session, [lessons.retire_lesson(session, lesson_id, actor_id=admin.id)])[0]
