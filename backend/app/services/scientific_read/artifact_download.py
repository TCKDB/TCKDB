"""Resolve downloadable calculation artifacts: approved, or the caller's own.

Two ways a stored object becomes retrievable, and they answer different
questions. *Approved* means a curator has published this evidence, so any
authenticated caller may pull it. *Owned* means the caller deposited it,
so refusing them their own file protects nobody.

Approval alone was the whole rule until 2026-08-24, and the effect was
perverse: measured on the hosted instance, 563 of 563 artifacts belonged
to calculations still ``not_reviewed``, so the gate had never once opened
— including for the person who uploaded the bytes. ADR 0004's reasoning
for gating raw logs (they carry scratch paths, usernames and scheduler ids
that cannot be scrubbed at rest without breaking content-addressing) is
untouched: the authentication gate stays unconditional, and nothing here
becomes anonymous. Only the *review-status* half of the gate moved.
"""

from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationArtifact
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview
from app.services.deposit_ownership import user_owns_calculation_deposit


def resolve_approved_artifact_by_sha256(
    session: Session, sha256: str
) -> CalculationArtifact | None:
    """Return a deterministic approved artifact row for a content digest.

    Artifact review visibility is inherited from the owning calculation. A
    digest is downloadable by any authenticated caller only when at least
    one attached calculation has an explicit ``approved`` review state.
    Duplicate upload-event rows can point at the same content-addressed
    object; the earliest approved row supplies the filename and expected
    byte count.
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


def resolve_downloadable_artifact_by_sha256(
    session: Session, sha256: str, user: AppUser
) -> CalculationArtifact | None:
    """Return an artifact row *user* may download, or ``None``.

    Approved first, so an approved digest resolves identically for every
    caller and the common path is one query. Failing that, the caller's own
    deposits: the earliest row for this digest whose owning calculation
    they deposited, judged by
    :func:`~app.services.deposit_ownership.user_owns_calculation_deposit`
    — the same predicate the upload route authorizes with, so a file the
    caller was allowed to attach is a file they are allowed to fetch back.

    Ownership is checked per candidate row rather than folded into the
    SQL above deliberately. Expressing "is this mine" a second time, in a
    dialect where it could quietly drift from the first, is how a store
    ends up with two authorization rules that disagree. The loop is
    bounded by the number of upload *events* sharing one digest, which is
    small by construction (max 5 across the hosted instance, mean 1.4 on
    2026-08-24) — rows are per-event, not per-user.

    ``None`` means "nothing here for you" and is deliberately not
    distinguishable by the caller from an unknown digest; the route
    answers 404 either way.
    """

    approved = resolve_approved_artifact_by_sha256(session, sha256)
    if approved is not None:
        return approved

    candidates = session.scalars(
        select(CalculationArtifact)
        .where(
            CalculationArtifact.sha256 == sha256,
            CalculationArtifact.bytes.is_not(None),
        )
        .order_by(CalculationArtifact.id.asc())
    ).all()

    for artifact in candidates:
        if user_owns_calculation_deposit(session, artifact.calculation, user):
            return artifact

    return None


__all__ = [
    "resolve_approved_artifact_by_sha256",
    "resolve_downloadable_artifact_by_sha256",
]
