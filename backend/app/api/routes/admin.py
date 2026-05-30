"""Admin-only management endpoints.

Scope in v1 is intentionally tiny: role changes plus a private machine-review
inspection endpoint.  Everything here is gated behind the ``admin`` role —
curators cannot promote each other, and the machine-review inspection endpoint
is deliberately admin-only (the stricter of the two existing gates) because it
is a debugging surface, not public scientific trust.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_write_db, require_admin
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.db.models.submission import Submission
from app.services.machine_review import (
    MachineReviewRecordSummary,
    SubmissionMachineReviewInspection,
    build_submission_machine_review_inspection,
)

router = APIRouter()


class RoleChangeRequest(BaseModel):
    role: AppUserRole


class UserRoleResponse(BaseModel):
    id: int
    username: str
    role: AppUserRole


@router.patch("/users/{user_id}/role", response_model=UserRoleResponse)
def change_user_role(
    user_id: int,
    request: RoleChangeRequest,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> UserRoleResponse:
    user = session.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.role = request.role
    session.flush()
    return UserRoleResponse(id=user.id, username=user.username, role=user.role)


# ---------------------------------------------------------------------------
# Private machine-review inspection (admin debugging only)
# ---------------------------------------------------------------------------
#
# This endpoint surfaces how a submission's existing ``llm_precheck_recorded``
# audit events project onto the records linked to that submission, reusing the
# private machine-review stack (audit adapter -> safe mapping -> read model). It
# is a debugging aid for maintainers deciding whether to expose
# ``trust.machine_review`` publicly later. It is **not** public scientific
# trust: nothing here touches the public ``TrustFragment`` or the scientific
# read routes, and the response is its own admin-only schema. The handler reads
# through ``get_db`` (no write session) and mutates nothing.


class AdminMachineReviewRecordInspection(BaseModel):
    """Admin-only machine-review projection for one linked record."""

    model_config = ConfigDict(extra="forbid")

    record_type: str
    record_ref: str | None = None
    record_id: int | None = None
    latest_summary: MachineReviewRecordSummary
    all_record_reviews_count: int = 0


class AdminSubmissionMachineReviewInspectionResponse(BaseModel):
    """Admin-only machine-review inspection response for one submission.

    Private/debugging shape — intentionally distinct from the public
    scientific ``TrustFragment``. ``record_summaries`` carries one entry per
    linked record that received at least one mapped machine-review finding;
    submission-scoped, unlinked, and sibling findings appear only in the
    diagnostics counters/warnings, never as a record summary.
    """

    model_config = ConfigDict(extra="forbid")

    submission_id: int
    record_summaries: tuple[AdminMachineReviewRecordInspection, ...] = ()
    unmapped_findings_count: int = 0
    mapping_warnings: tuple[str, ...] = ()
    parse_warnings: tuple[str, ...] = ()
    source_audit_event_ids: tuple[int, ...] = ()


def _to_admin_inspection_response(
    inspection: SubmissionMachineReviewInspection,
) -> AdminSubmissionMachineReviewInspectionResponse:
    """Map the private inspection result onto the admin response schema."""
    return AdminSubmissionMachineReviewInspectionResponse(
        submission_id=inspection.submission_id,
        record_summaries=tuple(
            AdminMachineReviewRecordInspection(
                record_type=record.record_type,
                record_ref=record.record_ref,
                record_id=record.record_id,
                latest_summary=record.latest_summary,
                all_record_reviews_count=len(record.all_record_reviews),
            )
            for record in inspection.record_inspections
        ),
        unmapped_findings_count=len(inspection.unmapped_findings),
        mapping_warnings=inspection.mapping_warnings,
        parse_warnings=inspection.parse_warnings,
        source_audit_event_ids=inspection.source_audit_event_ids,
    )


@router.get(
    "/submissions/{submission_id}/machine-review-inspection",
    response_model=AdminSubmissionMachineReviewInspectionResponse,
)
def inspect_submission_machine_review(
    submission_id: int,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminSubmissionMachineReviewInspectionResponse:
    """Inspect machine-review projections for one submission (admin only).

    Loads the submission (404 if missing), then projects its machine-review
    audit events onto its linked records via the private inspection service.
    Non-machine-review events are ignored. This is read-only: it never mutates
    submission status/lifecycle fields, review state, deterministic evidence,
    or scientific records.
    """
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found.")

    inspection = build_submission_machine_review_inspection(
        submission_id=submission.id,
        submission_record_links=submission.record_links,
        submission_audit_events=submission.audit_events,
    )
    return _to_admin_inspection_response(inspection)
