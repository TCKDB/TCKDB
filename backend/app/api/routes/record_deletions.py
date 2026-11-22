"""Admin deletion of approved scientific records, and the deletion audit.

    POST   /api/v1/admin/record-deletions
    GET    /api/v1/admin/record-deletions
    GET    /api/v1/admin/record-deletions/{public_ref}

Admin only: curators and depositors get 403, anonymous callers 401. A record that was never approved is out of scope
(409 ``record_deletion_not_approved``); the record-review reject flow handles it. See
:mod:`app.services.record_deletion` for what is removed, what refuses, and how the database allows it.

Deleting a record creates its audit entry, so the operation is a ``POST`` to the audit collection: a ``DELETE`` would
need a request body for the reason, and some proxies drop the body of a ``DELETE``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_write_db, require_admin
from app.api.routes._pagination import PaginatedResponse
from app.api.server_faults import ServerFaultRoute
from app.db.models.app_user import AppUser
from app.db.models.common import SubmissionRecordType
from app.db.models.record_deletion import RecordDeletion
from app.services.record_deletion import RecordDeletionError, delete_approved_record, find_deletion

router = APIRouter(route_class=ServerFaultRoute)


class RecordDeletionRequest(BaseModel):
    """Which approved record to delete, and why. The reason is required and kept in the audit."""

    model_config = ConfigDict(extra="forbid")

    record_type: SubmissionRecordType
    public_ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class SubjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_type: SubmissionRecordType
    public_ref: str


class RecordDeletionSummary(BaseModel):
    """One deletion as the audit records it. ``reason`` is the admin's own text."""

    model_config = ConfigDict(extra="forbid")

    record_type: SubmissionRecordType
    public_ref: str
    subject_type: SubmissionRecordType | None
    subject_public_ref: str | None
    subject_chain: list[SubjectRef]
    first_approved_at: datetime
    deleted_at: datetime
    deleted_by: str
    reason: str
    #: Rows removed, per table, counting the record itself under its own table.
    removed_rows: dict[str, int]


class RecordDeletionDetail(RecordDeletionSummary):
    """A deletion with the JSON copy of every removed row (row ids included: this surface is admin-only)."""

    snapshot: dict[str, Any]


def _removed_rows(deletion: RecordDeletion) -> dict[str, int]:
    counts = {deletion.record_type.value: 1}
    for section in ("owned", "overlays"):
        for table, rows in deletion.snapshot.get(section, {}).items():
            if rows:
                counts[table] = counts.get(table, 0) + len(rows)
    return counts


def _summary(deletion: RecordDeletion, username: str) -> dict[str, Any]:
    return {
        "record_type": deletion.record_type,
        "public_ref": deletion.record_public_ref,
        "subject_type": deletion.subject_type,
        "subject_public_ref": deletion.subject_public_ref,
        "subject_chain": deletion.subject_chain,
        "first_approved_at": deletion.first_approved_at,
        "deleted_at": deletion.deleted_at,
        "deleted_by": username,
        "reason": deletion.reason,
        "removed_rows": _removed_rows(deletion),
    }


@router.post("/record-deletions", response_model=RecordDeletionSummary, status_code=201)
def delete_record(
    body: RecordDeletionRequest,
    admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db, scope="function"),
) -> RecordDeletionSummary:
    """Delete one approved record and every row it owns (admin only).

    Refusals: 409 ``record_deletion_not_approved``, ``record_deletion_in_published_release`` (context lists the
    releases) and ``record_deletion_cited`` (context lists each citing record's type, public ref and the column that
    cites it); 422 ``record_deletion_type_not_supported``; 404 ``handle_not_found``; 410 ``record_deleted`` when the
    record is already gone.
    """
    deletion = delete_approved_record(
        session, record_type=body.record_type, public_ref=body.public_ref, reason=body.reason, actor=admin
    )
    return RecordDeletionSummary(**_summary(deletion, admin.username))


@router.get("/record-deletions", response_model=PaginatedResponse[RecordDeletionSummary])
def list_record_deletions(
    record_type: SubmissionRecordType | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> PaginatedResponse[RecordDeletionSummary]:
    """The deletion audit, newest first (admin only)."""
    filters = [] if record_type is None else [RecordDeletion.record_type == record_type]
    total = session.scalar(select(func.count()).select_from(RecordDeletion).where(*filters))
    rows = session.execute(
        select(RecordDeletion, AppUser.username)
        .join(AppUser, AppUser.id == RecordDeletion.deleted_by)
        .where(*filters)
        .order_by(RecordDeletion.deleted_at.desc(), RecordDeletion.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return PaginatedResponse(
        items=[RecordDeletionSummary(**_summary(deletion, username)) for deletion, username in rows],
        total=total or 0,
        skip=offset,
        limit=limit,
    )


@router.get("/record-deletions/{public_ref}", response_model=RecordDeletionDetail)
def get_record_deletion(
    public_ref: str = Path(..., min_length=1, max_length=40),
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> RecordDeletionDetail:
    """One deletion, with the JSON copy of the record and every row removed with it (admin only)."""
    deletion = find_deletion(session, public_ref=public_ref)
    if deletion is None:
        raise RecordDeletionError(
            status=404,
            code="handle_not_found",
            message=f"no record deletion is recorded for public ref {public_ref!r}.",
            context={"public_ref": public_ref},
        )
    username = session.scalar(select(AppUser.username).where(AppUser.id == deletion.deleted_by)) or ""
    return RecordDeletionDetail(**_summary(deletion, username), snapshot=deletion.snapshot)
