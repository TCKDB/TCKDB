"""Shared orchestration for turning a direct ``/uploads/*`` call into a
reviewable submission.

Every accepted API upload is a contribution event: it creates a
:class:`~app.db.models.submission.Submission` wrapper, runs the existing
per-family workflow under an ``under_review`` :class:`ReviewPolicy` that links
every produced record back to the submission, and appends an
``ingestion_succeeded`` audit event on success.

Usage in a route (flat, exception-safe by ordering)::

    sub = open_upload_submission(session, created_by=user.id,
                                 kind=SubmissionKind.conformer)
    outcome = persist_conformer_upload(
        session, request, created_by=user.id, review_policy=sub.policy
    )
    result = ConformerUploadResult(..., submission_id=sub.submission_id)
    mark_upload_ingested(session, sub)
    idem.record(...)

Transaction management stays with the route's ``get_write_db`` dependency. If
the wrapped workflow raises, control never reaches
:func:`mark_upload_ingested`, and the whole transaction — submission, audit,
record links, review rows, and scientific records — rolls back together. There
is therefore no orphan-submission state to clean up on the synchronous path,
and ``ingestion_failed`` is reserved for a future async/two-phase path that can
commit a failure record independently.

A submission is the audit wrapper for an upload event; it is *not* a claim of
scientific approval. ``submission.status`` stays ``pending`` (awaiting curator
review) and the records' ``record_review.status`` is ``under_review``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.common import (
    RecordReviewStatus,
    SubmissionKind,
    SubmissionSourceKind,
)
from app.db.models.submission import Submission
from app.services.record_review import ReviewPolicy
from app.services.submission import create_submission, mark_ingestion_succeeded


@dataclass
class UploadSubmissionContext:
    """Handle returned to an upload route for its submission scope."""

    submission: Submission
    policy: ReviewPolicy
    kind: SubmissionKind

    @property
    def submission_id(self) -> int:
        return self.submission.id


def open_upload_submission(
    session: Session,
    *,
    created_by: int,
    kind: SubmissionKind,
    title: Optional[str] = None,
    summary: Optional[str] = None,
) -> UploadSubmissionContext:
    """Create the submission shell and the review policy for one upload.

    The returned ``policy`` is ``ReviewPolicy(status=under_review,
    submission_id=..., link_records=True)`` — pass it to the per-family
    workflow so every produced record is initialised under review and linked
    to the submission. Call :func:`mark_upload_ingested` only after the
    workflow returns successfully.
    """
    submission = create_submission(
        session,
        created_by=created_by,
        submission_kind=kind,
        source_kind=SubmissionSourceKind.api,
        title=title,
        summary=summary,
    )
    policy = ReviewPolicy(
        status=RecordReviewStatus.under_review,
        submission_id=submission.id,
        link_records=True,
    )
    return UploadSubmissionContext(submission=submission, policy=policy, kind=kind)


def mark_upload_ingested(
    session: Session,
    sub: UploadSubmissionContext,
    *,
    summary: Optional[str] = None,
) -> None:
    """Append the ``ingestion_succeeded`` audit event for a finished upload.

    Status is unchanged (``pending``): successful ingestion is not scientific
    approval.
    """
    mark_ingestion_succeeded(
        session,
        submission=sub.submission,
        summary=summary or f"Ingested {sub.kind.value} upload via direct API.",
    )


__all__ = [
    "UploadSubmissionContext",
    "open_upload_submission",
    "mark_upload_ingested",
]
