"""Pydantic schemas for submission moderation records.

Read/write shapes for the curator-driven moderation flow exposed by
``app/api/routes/submissions.py``. The active moderation actions are
human curator decisions: approve, reject, supersede.

``SubmissionPrecheckRequest`` is included for completeness but is
reserved for a possible future automated-review feature — no HTTP route
currently accepts it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionPrecheckLabel,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.schemas.common import SchemaBase, TimestampedReadSchema


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


class SubmissionCreate(SchemaBase):
    """Fields a service layer caller provides to open a new submission.

    ``created_by`` is resolved from the authenticated user on the API side
    and is not accepted in the body.
    """

    submission_kind: SubmissionKind
    source_kind: SubmissionSourceKind = SubmissionSourceKind.api
    upload_job_id: str | None = None
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = None
    supersedes_submission_id: int | None = None


class SubmissionRead(TimestampedReadSchema):
    """Read schema for a persisted :class:`Submission`."""

    created_by: int
    submission_kind: SubmissionKind
    source_kind: SubmissionSourceKind
    upload_job_id: str | None = None
    status: SubmissionStatus
    title: str | None = None
    summary: str | None = None
    submitted_at: datetime
    approved_at: datetime | None = None
    approved_by: int | None = None
    rejected_at: datetime | None = None
    rejected_by: int | None = None
    rejection_reason: str | None = None
    correction_due_at: datetime | None = None
    supersedes_submission_id: int | None = None
    # llm_precheck_* are reserved for future optional automated review.
    # No current route or background process populates them; current
    # moderation is curator-driven.
    llm_precheck_label: SubmissionPrecheckLabel | None = None
    llm_precheck_summary: str | None = None
    llm_precheck_model: str | None = None
    llm_precheck_at: datetime | None = None
    is_public: bool


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------


class SubmissionAuditEventRead(BaseModel):
    """Read schema for one append-only audit event."""

    model_config = {"from_attributes": True}

    id: int
    submission_id: int
    created_at: datetime
    actor_user_id: int | None = None
    actor_kind: SubmissionActorKind
    event_kind: SubmissionAuditEventKind
    from_status: SubmissionStatus | None = None
    to_status: SubmissionStatus | None = None
    reason: str | None = None
    summary: str | None = None
    details_json: dict[str, Any] | None = None
    related_submission_id: int | None = None


# ---------------------------------------------------------------------------
# Record links
# ---------------------------------------------------------------------------


class SubmissionRecordLinkRead(TimestampedReadSchema):
    """Read schema for a submission-to-record link row."""

    submission_id: int
    record_type: SubmissionRecordType
    record_id: int
    role: str | None = None


# ---------------------------------------------------------------------------
# Moderation action payloads (service-facing; reused when API lands)
# ---------------------------------------------------------------------------


class SubmissionRejectRequest(SchemaBase):
    """Payload for a curator rejecting a submission."""

    reason: str = Field(min_length=1)
    summary: str | None = None
    correction_due_at: datetime | None = None


class SubmissionApproveRequest(SchemaBase):
    """Payload for a curator approving a submission."""

    summary: str | None = None


class SubmissionPrecheckRequest(SchemaBase):
    """Payload shape for recording an automated precheck result.

    Reserved for a possible future automated-review feature. No HTTP
    route currently accepts this payload — current moderation is
    curator-driven (see ``SubmissionApproveRequest`` /
    ``SubmissionRejectRequest``).
    """

    label: SubmissionPrecheckLabel
    model: str | None = Field(default=None, max_length=128)
    summary: str | None = None
    details_json: dict[str, Any] | None = None


class SubmissionSupersedeRequest(SchemaBase):
    """Payload for marking a submission as superseded by a newer one.

    The replacing submission must have been created with
    ``supersedes_submission_id`` pointing back at the old id; the service
    enforces that link rather than mutating it here.
    """

    new_submission_id: int
