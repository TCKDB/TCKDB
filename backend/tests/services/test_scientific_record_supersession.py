"""Focused service tests for accepted-science replacement chains."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import DBAPIError

from app.api.errors import DomainError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.network import Network
from app.db.models.record_review import RecordReview
from app.db.models.scientific_record_supersession import ScientificRecordSupersession
from app.services.record_review import ensure_record_review, set_record_review_status
from app.services.scientific_record_supersession import supersede_scientific_record


def _curator(session) -> AppUser:
    actor = AppUser(username="supersession-curator", role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return actor


def _network(session, name: str) -> Network:
    row = Network(name=name)
    session.add(row)
    session.flush()
    return row


def _approve(session, row: Network, actor: AppUser) -> RecordReview:
    ensure_record_review(
        session,
        record_type=SubmissionRecordType.network,
        record_id=row.id,
    )
    return set_record_review_status(
        session,
        record_type=SubmissionRecordType.network,
        record_id=row.id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )


def test_supersession_supports_linear_chains(db_session) -> None:
    actor = _curator(db_session)
    first, second, third = (
        _network(db_session, "first"),
        _network(db_session, "second"),
        _network(db_session, "third"),
    )
    reviews = [_approve(db_session, row, actor) for row in (first, second, third)]

    edge_one = supersede_scientific_record(
        db_session,
        record_type=SubmissionRecordType.network,
        superseded_record_id=first.id,
        superseding_record_id=second.id,
        actor=actor,
        reason="better network definition",
    )
    edge_two = supersede_scientific_record(
        db_session,
        record_type=SubmissionRecordType.network,
        superseded_record_id=second.id,
        superseding_record_id=third.id,
        actor=actor,
        reason="expanded pressure range",
    )

    assert (edge_one.superseded_record_id, edge_one.superseding_record_id) == (
        first.id,
        second.id,
    )
    assert (edge_two.superseded_record_id, edge_two.superseding_record_id) == (
        second.id,
        third.id,
    )
    assert reviews[0].status is RecordReviewStatus.deprecated
    assert reviews[1].status is RecordReviewStatus.deprecated
    assert reviews[2].status is RecordReviewStatus.approved


def test_exact_retry_requires_the_same_normalized_reason(db_session) -> None:
    actor = _curator(db_session)
    old, new = _network(db_session, "old"), _network(db_session, "new")
    _approve(db_session, old, actor)
    _approve(db_session, new, actor)
    first = supersede_scientific_record(
        db_session,
        record_type=SubmissionRecordType.network,
        superseded_record_id=old.id,
        superseding_record_id=new.id,
        actor=actor,
        reason="  corrected topology  ",
    )
    retry = supersede_scientific_record(
        db_session,
        record_type=SubmissionRecordType.network,
        superseded_record_id=old.id,
        superseding_record_id=new.id,
        actor=actor,
        reason="corrected topology",
    )
    assert retry.id == first.id

    with pytest.raises(DomainError, match="different reason"):
        supersede_scientific_record(
            db_session,
            record_type=SubmissionRecordType.network,
            superseded_record_id=old.id,
            superseding_record_id=new.id,
            actor=actor,
            reason="different explanation",
        )


def test_cycle_is_rejected_before_deprecating_old_record(db_session) -> None:
    actor = _curator(db_session)
    first, second, third = (
        _network(db_session, "cycle-first"),
        _network(db_session, "cycle-second"),
        _network(db_session, "cycle-third"),
    )
    for row in (first, second, third):
        _approve(db_session, row, actor)
    supersede_scientific_record(
        db_session,
        record_type=SubmissionRecordType.network,
        superseded_record_id=first.id,
        superseding_record_id=second.id,
        actor=actor,
        reason="first edge",
    )
    supersede_scientific_record(
        db_session,
        record_type=SubmissionRecordType.network,
        superseded_record_id=second.id,
        superseding_record_id=third.id,
        actor=actor,
        reason="second edge",
    )
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=first.id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )

    with pytest.raises(DomainError, match="cycle"):
        supersede_scientific_record(
            db_session,
            record_type=SubmissionRecordType.network,
            superseded_record_id=third.id,
            superseding_record_id=first.id,
            actor=actor,
            reason="would close a cycle",
        )
    assert third.name == "cycle-third"


def test_direct_edge_insert_requires_old_to_be_deprecated(db_session) -> None:
    actor = _curator(db_session)
    old, new = _network(db_session, "direct-old"), _network(db_session, "direct-new")
    _approve(db_session, old, actor)
    _approve(db_session, new, actor)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            ScientificRecordSupersession(
                record_type=SubmissionRecordType.network,
                superseded_record_id=old.id,
                superseding_record_id=new.id,
                reason="bypasses service",
                created_by=actor.id,
            )
        )
        db_session.flush()


@pytest.mark.parametrize(
    "status",
    [
        RecordReviewStatus.not_reviewed,
        RecordReviewStatus.under_review,
        RecordReviewStatus.rejected,
        RecordReviewStatus.deprecated,
        RecordReviewStatus.approved,
    ],
)
def test_review_with_approval_history_can_be_restored_at_any_status(db_session, status) -> None:
    actor = _curator(db_session)
    approved_at = datetime(2025, 1, 2, 3, 4, 5)
    review = RecordReview(
        record_type=SubmissionRecordType.species,
        record_id=987654,
        status=status,
        reviewed_by=actor.id,
        reviewed_at=approved_at,
        first_approved_at=approved_at,
    )
    db_session.add(review)
    db_session.flush()
    assert review.first_approved_at == approved_at
