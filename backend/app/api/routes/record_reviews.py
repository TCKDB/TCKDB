"""HTTP API for per-record review/trust state.

Read endpoints are visible to any authenticated user; the manual PATCH
endpoint is curator/admin-gated and routes through
``app/services/record_review.py`` so the transition policy and
self-approval guard apply uniformly with every other writer of this
table.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import (
    PaginationParams,
    get_current_user,
    get_db,
    get_write_db,
    require_curator_or_admin,
)
from app.db.models.app_user import AppUser
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.entities.record_review import (
    RecordReviewRead,
    RecordReviewSetStatusRequest,
)
from app.services.record_review import (
    get_record_review,
    list_record_reviews,
    set_record_review_status,
)

router = APIRouter()


@router.get("", response_model=list[RecordReviewRead])
def list_reviews(
    record_type: SubmissionRecordType | None = Query(default=None),
    status: RecordReviewStatus | None = Query(default=None),
    submission_id: int | None = Query(default=None),
    pagination: PaginationParams = Depends(),
    session: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> list[RecordReviewRead]:
    """List review rows newest-first, filterable by type/status/submission."""
    rows = list_record_reviews(
        session,
        record_type=record_type,
        status=status,
        submission_id=submission_id,
        limit=pagination.limit,
        offset=pagination.skip,
    )
    return [RecordReviewRead.model_validate(r) for r in rows]


@router.get(
    "/{record_type}/{record_id}",
    response_model=RecordReviewRead,
)
def read_review(
    record_type: SubmissionRecordType,
    record_id: int,
    session: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> RecordReviewRead:
    """Return the current review row for ``(record_type, record_id)``.

    404 if no review row has ever been written for that pair (clients
    should treat that as ``not_reviewed`` if and only if they have
    independently confirmed the underlying record exists).
    """
    row = get_record_review(
        session, record_type=record_type, record_id=record_id
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No review row found for this record.",
        )
    return RecordReviewRead.model_validate(row)


@router.patch(
    "/{record_type}/{record_id}",
    response_model=RecordReviewRead,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def set_status(
    record_type: SubmissionRecordType,
    record_id: int,
    body: RecordReviewSetStatusRequest,
    session: Session = Depends(get_write_db),
    actor: AppUser = Depends(require_curator_or_admin),
) -> RecordReviewRead:
    """Curator/admin: manually transition a record's review status.

    The transition policy and self-approval guard live in the service
    layer; route-side authorisation is the curator/admin role check
    only.
    """
    row = set_record_review_status(
        session,
        record_type=record_type,
        record_id=record_id,
        status=body.status,
        actor=actor,
        submission_id=body.submission_id,
        note=body.note,
    )
    return RecordReviewRead.model_validate(row)
