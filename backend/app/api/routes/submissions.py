"""HTTP API for the submission moderation lifecycle.

These endpoints are the public interface to ``app/services/submission.py``.
They never create scientific records themselves — moderated contribution
ingestion still flows through ``/api/v1/bundles/submit``, which wraps the
underlying scientific workflows with submission/audit/link writes. This
module only exposes:

* read access (``GET /mine``, ``GET /for-review``, ``GET /{id}``,
  ``GET /{id}/audit-events``, ``GET /{id}/record-links``), and
* curator actions (``POST /{id}/approve``, ``POST /{id}/reject``,
  ``POST /{id}/supersede``).

Direct ``/uploads/*`` ingestion is intentionally NOT routed through this
module — trusted ingest stays free of moderation overhead.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import (
    get_current_user,
    get_db,
    get_write_db,
    require_curator_or_admin,
)
from app.db.models.app_user import AppUser, AppUserRole
from app.db.models.common import SubmissionStatus
from app.db.models.submission import Submission
from app.schemas.entities.submission import (
    SubmissionApproveRequest,
    SubmissionAuditEventRead,
    SubmissionRead,
    SubmissionRecordLinkRead,
    SubmissionRejectRequest,
    SubmissionSupersedeRequest,
)
from app.services.submission import (
    approve_submission,
    get_submission,
    list_audit_events,
    list_my_submissions,
    list_record_links,
    list_submissions_for_review,
    reject_submission,
    supersede_submission,
)

router = APIRouter()


_CURATION_ROLES = frozenset({AppUserRole.curator, AppUserRole.admin})


def _can_view(submission: Submission, user: AppUser) -> bool:
    return user.role in _CURATION_ROLES or submission.created_by == user.id


def _require_view_permission(submission: Submission, user: AppUser) -> None:
    if not _can_view(submission, user):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view this submission.",
        )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@router.get("/mine", response_model=list[SubmissionRead])
def list_mine(
    statuses: list[SubmissionStatus] | None = Query(default=None),
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[SubmissionRead]:
    """List submissions created by the calling user, newest first."""
    rows = list_my_submissions(
        session, user_id=current_user.id, statuses=statuses
    )
    return [SubmissionRead.model_validate(r) for r in rows]


@router.get("/for-review", response_model=list[SubmissionRead])
def list_for_review(
    statuses: list[SubmissionStatus] | None = Query(default=None),
    session: Session = Depends(get_db),
    _curator: AppUser = Depends(require_curator_or_admin),
) -> list[SubmissionRead]:
    """List submissions awaiting curator review (curator/admin only)."""
    rows = list_submissions_for_review(session, statuses=statuses)
    return [SubmissionRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Read one
# ---------------------------------------------------------------------------


@router.get("/{submission_id}", response_model=SubmissionRead)
def read_submission(
    submission_id: int,
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> SubmissionRead:
    """Read a single submission. Visible to its creator and curators/admins."""
    submission = get_submission(session, submission_id)
    _require_view_permission(submission, current_user)
    return SubmissionRead.model_validate(submission)


@router.get(
    "/{submission_id}/audit-events",
    response_model=list[SubmissionAuditEventRead],
)
def read_audit_events(
    submission_id: int,
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[SubmissionAuditEventRead]:
    """Return the append-only audit trail for a submission, oldest first."""
    submission = get_submission(session, submission_id)
    _require_view_permission(submission, current_user)
    events = list_audit_events(session, submission_id=submission_id)
    return [SubmissionAuditEventRead.model_validate(e) for e in events]


@router.get(
    "/{submission_id}/record-links",
    response_model=list[SubmissionRecordLinkRead],
)
def read_record_links(
    submission_id: int,
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[SubmissionRecordLinkRead]:
    """Return scientific record links produced by this submission."""
    submission = get_submission(session, submission_id)
    _require_view_permission(submission, current_user)
    links = list_record_links(session, submission_id=submission_id)
    return [SubmissionRecordLinkRead.model_validate(l) for l in links]


# ---------------------------------------------------------------------------
# Curator actions
# ---------------------------------------------------------------------------


@router.post(
    "/{submission_id}/approve",
    response_model=SubmissionRead,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def approve(
    submission_id: int,
    body: SubmissionApproveRequest | None = None,
    session: Session = Depends(get_write_db),
    actor: AppUser = Depends(require_curator_or_admin),
) -> SubmissionRead:
    """Approve a submission. Curator/admin only; uploader cannot self-approve."""
    summary = body.summary if body is not None else None
    submission = approve_submission(
        session, submission_id=submission_id, actor=actor, summary=summary
    )
    return SubmissionRead.model_validate(submission)


@router.post(
    "/{submission_id}/reject",
    response_model=SubmissionRead,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def reject(
    submission_id: int,
    body: SubmissionRejectRequest,
    session: Session = Depends(get_write_db),
    actor: AppUser = Depends(require_curator_or_admin),
) -> SubmissionRead:
    """Reject a submission with a required reason. Curator/admin only."""
    submission = reject_submission(
        session,
        submission_id=submission_id,
        actor=actor,
        reason=body.reason,
        summary=body.summary,
        correction_due_at=body.correction_due_at,
    )
    return SubmissionRead.model_validate(submission)


@router.post(
    "/{submission_id}/supersede",
    response_model=SubmissionRead,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def supersede(
    submission_id: int,
    body: SubmissionSupersedeRequest,
    session: Session = Depends(get_write_db),
    actor: AppUser = Depends(get_current_user),
) -> SubmissionRead:
    """Mark a submission as superseded by another.

    The replacing submission must already declare ``supersedes_submission_id``
    pointing back at this one — supersession asserts the link, it does not
    create it. Returns the newly-superseded *old* submission.
    """
    old = supersede_submission(
        session,
        old_submission_id=submission_id,
        new_submission_id=body.new_submission_id,
        actor=actor,
    )
    return SubmissionRead.model_validate(old)
