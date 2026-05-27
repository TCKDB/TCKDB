"""Context builder for optional AI Review Assistant prechecks."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.llm_precheck.schemas import LLMPrecheckContext, LLMRecordRef


def build_llm_precheck_context(
    session: Session,
    submission_id: int,
) -> LLMPrecheckContext:
    """Build a compact structured context for optional AI Review Assistant precheck."""
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise NotFoundError(f"Submission {submission_id} not found")

    links = session.scalars(
        select(SubmissionRecordLink)
        .where(SubmissionRecordLink.submission_id == submission_id)
        .order_by(SubmissionRecordLink.id.asc())
    ).all()

    return LLMPrecheckContext(
        submission_id=submission.id,
        submission_status=submission.status.value,
        submission_kind=submission.submission_kind.value,
        source_kind=submission.source_kind.value,
        title=submission.title,
        summary=submission.summary,
        record_refs=tuple(
            LLMRecordRef(
                record_type=link.record_type.value,
                record_id=link.record_id,
                role=link.role,
            )
            for link in links
        ),
        trust_summaries=(),
        included_artifact_text=False,
        included_coordinates=False,
        included_private_notes=False,
    )
