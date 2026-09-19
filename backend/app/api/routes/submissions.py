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
from app.db.models.submission_rights import SubmissionRightsAttestation
from app.schemas.entities.submission import (
    RightsAttestationActor,
    RightsAttestationCreate,
    RightsAttestationRead,
    SubmissionAIReviewFindingCounts,
    SubmissionAIReviewSummaryRead,
    SubmissionApproveRequest,
    SubmissionAuditEventRead,
    SubmissionRead,
    SubmissionRecordLinkRead,
    SubmissionRejectRequest,
    SubmissionSupersedeRequest,
)
from app.services.rights import (
    RightsAttestationError,
    RightsAttestationForbidden,
    list_attestations,
    record_attestation,
)
from app.services.submission import (
    approve_submission,
    get_latest_llm_precheck_audit_event,
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
_AI_REVIEW_FINDING_SEVERITIES = ("info", "warning", "critical")


def _can_view(submission: Submission, user: AppUser) -> bool:
    return user.role in _CURATION_ROLES or submission.created_by == user.id


def _require_view_permission(submission: Submission, user: AppUser) -> None:
    if not _can_view(submission, user):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view this submission.",
        )


def _ai_review_summary_from_event(event) -> SubmissionAIReviewSummaryRead:
    details = event.details_json if isinstance(event.details_json, dict) else {}
    findings = details.get("findings")
    if not isinstance(findings, list):
        findings = []

    counts = dict.fromkeys(_AI_REVIEW_FINDING_SEVERITIES, 0)
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = finding.get("severity")
        if severity in counts:
            counts[severity] += 1

    return SubmissionAIReviewSummaryRead(
        label=details.get("label") if isinstance(details.get("label"), str) else None,
        summary=(
            details.get("summary")
            if isinstance(details.get("summary"), str)
            else event.summary
        ),
        model=details.get("model") if isinstance(details.get("model"), str) else None,
        used_rag=(
            details.get("used_rag")
            if isinstance(details.get("used_rag"), bool)
            else None
        ),
        created_at=event.created_at,
        finding_counts=SubmissionAIReviewFindingCounts(**counts),
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@router.get("/mine", response_model=list[SubmissionRead])
def list_mine(
    statuses: list[SubmissionStatus] | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[SubmissionRead]:
    """List submissions created by the calling user, newest first."""
    rows = list_my_submissions(
        session, user_id=current_user.id, statuses=statuses, offset=offset, limit=limit
    )
    return [SubmissionRead.model_validate(r) for r in rows]


@router.get("/for-review", response_model=list[SubmissionRead])
def list_for_review(
    statuses: list[SubmissionStatus] | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
    _curator: AppUser = Depends(require_curator_or_admin),
) -> list[SubmissionRead]:
    """List submissions awaiting curator review (curator/admin only)."""
    rows = list_submissions_for_review(session, statuses=statuses, offset=offset, limit=limit)
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
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[SubmissionAuditEventRead]:
    """Return the append-only audit trail for a submission, oldest first."""
    submission = get_submission(session, submission_id)
    _require_view_permission(submission, current_user)
    events = list_audit_events(session, submission_id=submission_id, offset=offset, limit=limit)
    return [SubmissionAuditEventRead.model_validate(e) for e in events]


@router.get(
    "/{submission_id}/ai-review-summary",
    response_model=SubmissionAIReviewSummaryRead | None,
)
def read_ai_review_summary(
    submission_id: int,
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> SubmissionAIReviewSummaryRead | None:
    """Return the latest compact advisory AI review card for a submission."""
    submission = get_submission(session, submission_id)
    _require_view_permission(submission, current_user)
    event = get_latest_llm_precheck_audit_event(
        session, submission_id=submission_id
    )
    if event is None:
        return None
    return _ai_review_summary_from_event(event)


@router.get(
    "/{submission_id}/record-links",
    response_model=list[SubmissionRecordLinkRead],
)
def read_record_links(
    submission_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[SubmissionRecordLinkRead]:
    """Return scientific record links produced by this submission."""
    submission = get_submission(session, submission_id)
    _require_view_permission(submission, current_user)
    links = list_record_links(session, submission_id=submission_id, offset=offset, limit=limit)
    return [SubmissionRecordLinkRead.model_validate(link) for link in links]


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


# ---------------------------------------------------------------------------
# Rights attestations
# ---------------------------------------------------------------------------


def _attestation_records(
    rows: list[SubmissionRightsAttestation], submission: Submission
) -> list[RightsAttestationRead]:
    """Render the chain with refs only: no user id, no row id."""
    by_id = {row.id: row for row in rows}
    replaced_by = {
        row.supersedes_attestation_id: row
        for row in rows
        if row.supersedes_attestation_id is not None
    }
    out: list[RightsAttestationRead] = []
    for row in rows:
        superseded = by_id.get(row.supersedes_attestation_id or -1)
        replacement = replaced_by.get(row.id)
        attester = row.attester
        out.append(
            RightsAttestationRead(
                attestation_ref=row.public_ref,
                submission_ref=submission.public_ref,
                license=row.license_id,
                basis=row.basis,
                actor_kind=row.actor_kind,
                attested_by=RightsAttestationActor(
                    username=attester.username,
                    full_name=attester.full_name,
                    orcid=attester.orcid,
                    affiliation=attester.affiliation,
                ),
                attested_at=row.attested_at,
                source_terms=row.source_terms,
                note=row.note,
                supersedes_attestation_ref=(
                    superseded.public_ref if superseded is not None else None
                ),
                superseded_by_attestation_ref=(
                    replacement.public_ref if replacement is not None else None
                ),
                stands=replacement is None,
            )
        )
    return out


@router.get(
    "/{submission_id}/rights-attestations",
    response_model=list[RightsAttestationRead],
)
def read_rights_attestations(
    submission_id: int,
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[RightsAttestationRead]:
    """Every rights attestation ever recorded for a submission, oldest first.

    Visible to the submission's creator and to curators/admins, like the rest
    of the submission. Superseded rows stay listed with ``stands: false``.
    """
    submission = get_submission(session, submission_id)
    _require_view_permission(submission, current_user)
    rows = list_attestations(session, submission_id=submission.id)
    return _attestation_records(rows, submission)


@router.post(
    "/{submission_id}/rights-attestations",
    response_model=RightsAttestationRead,
    status_code=201,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def create_rights_attestation(
    submission_id: int,
    body: RightsAttestationCreate,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
) -> RightsAttestationRead:
    """Record who agrees to license this submission, and on what basis.

    A ``depositor_agreement`` is the depositor's own statement and is accepted
    only from the submission's creator; ``operator_own_data``,
    ``historical_review`` and ``source_terms`` record a curator's judgement
    and need the curator or admin role. Appending never edits: a new row
    supersedes the one that stood before.

    :raises HTTPException: 403 when the caller may not make this attestation;
        422 for a body the caller can correct (blank license, missing
        ``source_terms``).
    """
    submission = get_submission(session, submission_id)
    if not _can_view(submission, current_user):
        # Same answer as every other route on this router for a submission
        # the caller has no business with: not "who may attest", just "no".
        raise HTTPException(
            status_code=403, detail="Not authorized to view this submission."
        )
    try:
        row = record_attestation(
            session,
            submission=submission,
            license_id=body.license,
            basis=body.basis,
            actor=current_user,
            note=body.note,
            source_terms=body.source_terms,
        )
    except RightsAttestationError as exc:
        # One handler, two statuses: Forbidden is a subclass, and the
        # re-raise gate can address only one except per function.
        status = 403 if isinstance(exc, RightsAttestationForbidden) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    rows = list_attestations(session, submission_id=submission.id)
    return next(
        record
        for record in _attestation_records(rows, submission)
        if record.attestation_ref == row.public_ref
    )
