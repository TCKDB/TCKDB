"""Resolve publicly downloadable, curator-approved calculation artifacts."""

from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationArtifact
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview


def resolve_approved_artifact_by_sha256(
    session: Session, sha256: str
) -> CalculationArtifact | None:
    """Return a deterministic approved artifact row for a content digest.

    Artifact review visibility is inherited from the owning calculation. A
    digest is downloadable only when at least one attached calculation has an
    explicit ``approved`` review state. Duplicate upload-event rows can point
    at the same content-addressed object; the earliest approved row supplies
    the filename and expected byte count.
    """

    return session.scalar(
        select(CalculationArtifact)
        .join(
            RecordReview,
            and_(
                RecordReview.record_type == SubmissionRecordType.calculation,
                RecordReview.record_id == CalculationArtifact.calculation_id,
            ),
        )
        .where(
            CalculationArtifact.sha256 == sha256,
            CalculationArtifact.bytes.is_not(None),
            RecordReview.status == RecordReviewStatus.approved,
        )
        .order_by(CalculationArtifact.id.asc())
        .limit(1)
    )


__all__ = ["resolve_approved_artifact_by_sha256"]
