"""Curator workflow for replacing an accepted scientific record."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.db.models.scientific_record_supersession import ScientificRecordSupersession
from app.services.accepted_science import (
    is_accepted_science_type,
    lock_scientific_records,
    supersession_subject,
)
from app.services.record_review import set_record_review_status

_CURATION_ROLES = frozenset({AppUserRole.curator, AppUserRole.admin})


@dataclass(frozen=True)
class _Ref:
    record_type: SubmissionRecordType
    record_id: int


def supersede_scientific_record(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    superseded_record_id: int,
    superseding_record_id: int,
    actor: AppUser,
    reason: str,
) -> ScientificRecordSupersession:
    """Append a replacement edge and deprecate its formerly accepted record.

    Both roots and their review rows are locked before validation. The
    operation is idempotent for an already-recorded identical edge and never
    commits; the caller owns the transaction boundary.
    """

    if actor.role not in _CURATION_ROLES:
        raise DomainError("Curator or admin role required for this action")
    reason = reason.strip()
    if not reason:
        raise DomainError("Supersession reason must not be blank")
    if not is_accepted_science_type(record_type):
        raise DomainError(f"Unsupported supersession type: {record_type.value}")
    if superseded_record_id == superseding_record_id:
        raise DomainError("A scientific record cannot supersede itself")

    refs = (
        _Ref(record_type, superseded_record_id),
        _Ref(record_type, superseding_record_id),
    )
    roots = lock_scientific_records(session, refs)

    existing_edges = list(
        session.scalars(
            select(ScientificRecordSupersession)
            .where(ScientificRecordSupersession.record_type == record_type)
            .order_by(ScientificRecordSupersession.id)
        ).all()
    )
    for edge in existing_edges:
        if edge.superseded_record_id == superseded_record_id and edge.superseding_record_id == superseding_record_id:
            if edge.reason == reason:
                return edge
            raise DomainError("The supersession edge already exists with a different reason")
    if any(
        edge.superseded_record_id == superseded_record_id or edge.superseding_record_id == superseding_record_id
        for edge in existing_edges
    ):
        raise DomainError("A supersession edge already uses one of these record endpoints")
    successors = {edge.superseded_record_id: edge.superseding_record_id for edge in existing_edges}
    cursor = superseding_record_id
    visited: set[int] = set()
    while cursor in successors and cursor not in visited:
        if cursor == superseded_record_id:
            raise DomainError("Scientific supersession cannot create a cycle")
        visited.add(cursor)
        cursor = successors[cursor]
    if cursor == superseded_record_id:
        raise DomainError("Scientific supersession cannot create a cycle")

    reviews = list(
        session.scalars(
            select(RecordReview)
            .where(
                RecordReview.record_type == record_type,
                RecordReview.record_id.in_((superseded_record_id, superseding_record_id)),
            )
            .order_by(RecordReview.record_id)
            .with_for_update()
        ).all()
    )
    review_by_id = {review.record_id: review for review in reviews}
    old_review = review_by_id.get(superseded_record_id)
    new_review = review_by_id.get(superseding_record_id)
    if old_review is None or old_review.first_approved_at is None:
        raise DomainError("The superseded record has no approval history")
    if old_review.status not in {
        RecordReviewStatus.approved,
        RecordReviewStatus.deprecated,
    }:
        raise DomainError("The superseded record must be approved or deprecated")
    if new_review is None or new_review.status is not RecordReviewStatus.approved:
        raise DomainError("The superseding record must currently be approved")

    old_root = roots[(record_type, superseded_record_id)]
    new_root = roots[(record_type, superseding_record_id)]
    if supersession_subject(old_root, record_type) != supersession_subject(new_root, record_type):
        raise DomainError("Supersession records must describe the same subject")

    if old_review.status is RecordReviewStatus.approved:
        set_record_review_status(
            session,
            record_type=record_type,
            record_id=superseded_record_id,
            status=RecordReviewStatus.deprecated,
            actor=actor,
            note=reason,
        )

    edge = ScientificRecordSupersession(
        record_type=record_type,
        superseded_record_id=superseded_record_id,
        superseding_record_id=superseding_record_id,
        reason=reason,
        created_by=actor.id,
    )
    session.add(edge)
    session.flush()
    return edge
