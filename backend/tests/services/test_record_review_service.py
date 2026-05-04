"""Service-layer tests for the record_review module.

Cover:

* idempotent ``ensure_record_review`` insert,
* transition-policy enforcement (allowed and disallowed transitions),
* curator/admin role gate,
* self-approval guard scoped to ``approved`` only,
* terminal-status reviewer/timestamp stamping,
* bulk variant.
"""

from __future__ import annotations

import pytest

from app.api.errors import DomainError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview
from app.services.record_review import (
    RecordRef,
    bulk_set_record_review_status,
    ensure_record_review,
    get_record_review,
    list_record_reviews,
    set_record_review_status,
)


def _user(session, username: str, role: AppUserRole) -> AppUser:
    user = AppUser(username=username, role=role)
    session.add(user)
    session.flush()
    return user


class TestEnsureRecordReview:
    def test_inserts_row_at_not_reviewed(self, db_session):
        alice = _user(db_session, "alice", AppUserRole.user)
        row = ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=42,
            created_by=alice.id,
        )
        assert row.status is RecordReviewStatus.not_reviewed
        assert row.created_by == alice.id
        assert row.reviewed_by is None
        assert row.reviewed_at is None

    def test_idempotent_returns_existing(self, db_session):
        alice = _user(db_session, "alice", AppUserRole.user)
        first = ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.kinetics,
            record_id=7,
            created_by=alice.id,
        )
        second = ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.kinetics,
            record_id=7,
            created_by=alice.id,
        )
        assert first.id == second.id

    def test_terminal_default_rejected(self, db_session):
        with pytest.raises(DomainError):
            ensure_record_review(
                db_session,
                record_type=SubmissionRecordType.thermo,
                record_id=1,
                status=RecordReviewStatus.approved,
            )


class TestTransitionPolicy:
    def test_curator_role_required(self, db_session):
        alice = _user(db_session, "alice", AppUserRole.user)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=11,
            created_by=alice.id,
        )
        with pytest.raises(DomainError, match="Curator or admin"):
            set_record_review_status(
                db_session,
                record_type=SubmissionRecordType.thermo,
                record_id=11,
                status=RecordReviewStatus.approved,
                actor=alice,
            )

    def test_self_approval_blocked(self, db_session):
        creator = _user(db_session, "uploader", AppUserRole.curator)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=12,
            created_by=creator.id,
        )
        with pytest.raises(DomainError, match="cannot approve a record they created"):
            set_record_review_status(
                db_session,
                record_type=SubmissionRecordType.thermo,
                record_id=12,
                status=RecordReviewStatus.approved,
                actor=creator,
            )

    def test_self_deprecation_allowed(self, db_session):
        creator = _user(db_session, "creator", AppUserRole.curator)
        # Need to reach approved first via another curator, then creator
        # deprecates their own record. Bootstrap by other curator.
        other = _user(db_session, "other", AppUserRole.curator)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=13,
            created_by=creator.id,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=13,
            status=RecordReviewStatus.approved,
            actor=other,
        )
        # Now the creator deprecates — allowed because only `approved`
        # is gated by the self-review guard.
        row = set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=13,
            status=RecordReviewStatus.deprecated,
            actor=creator,
        )
        assert row.status is RecordReviewStatus.deprecated

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (RecordReviewStatus.approved, RecordReviewStatus.rejected),
            (RecordReviewStatus.rejected, RecordReviewStatus.approved),
            (RecordReviewStatus.deprecated, RecordReviewStatus.rejected),
        ],
    )
    def test_disallowed_transitions(self, db_session, from_status, to_status):
        admin = _user(db_session, "admin1", AppUserRole.admin)
        # Bootstrap row directly into from_status. Approved/rejected/
        # deprecated all require reviewer stamps; we go via not_reviewed.
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=14,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=14,
            status=from_status,
            actor=admin,
        )
        with pytest.raises(DomainError, match="Disallowed record-review"):
            set_record_review_status(
                db_session,
                record_type=SubmissionRecordType.thermo,
                record_id=14,
                status=to_status,
                actor=admin,
            )

    def test_terminal_stamps_reviewer_and_clears_on_return(self, db_session):
        admin = _user(db_session, "admin", AppUserRole.admin)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=15,
        )
        approved = set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=15,
            status=RecordReviewStatus.approved,
            actor=admin,
        )
        assert approved.reviewed_by == admin.id
        assert approved.reviewed_at is not None

        # Move back to under_review — reviewer/timestamp should clear
        # for clean semantics.
        reopened = set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=15,
            status=RecordReviewStatus.under_review,
            actor=admin,
        )
        assert reopened.reviewed_by is None
        assert reopened.reviewed_at is None


class TestUniqueConstraint:
    def test_one_row_per_record(self, db_session):
        from sqlalchemy.exc import IntegrityError

        first = RecordReview(
            record_type=SubmissionRecordType.thermo,
            record_id=99,
            status=RecordReviewStatus.not_reviewed,
        )
        db_session.add(first)
        db_session.flush()

        dup = RecordReview(
            record_type=SubmissionRecordType.thermo,
            record_id=99,
            status=RecordReviewStatus.not_reviewed,
        )
        db_session.add(dup)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()


class TestBulk:
    def test_bulk_set_status_uses_actor_per_call(self, db_session):
        admin = _user(db_session, "admin-bulk", AppUserRole.admin)
        targets = [
            RecordRef(SubmissionRecordType.thermo, 200),
            RecordRef(SubmissionRecordType.thermo, 201),
            RecordRef(SubmissionRecordType.thermo, 202),
        ]
        for t in targets:
            ensure_record_review(
                db_session, record_type=t.record_type, record_id=t.record_id
            )
        rows = bulk_set_record_review_status(
            db_session,
            targets=targets,
            status=RecordReviewStatus.approved,
            actor=admin,
        )
        assert len(rows) == 3
        for r in rows:
            assert r.status is RecordReviewStatus.approved
            assert r.reviewed_by == admin.id


class TestList:
    def test_filters(self, db_session):
        admin = _user(db_session, "admin-list", AppUserRole.admin)
        ensure_record_review(
            db_session, record_type=SubmissionRecordType.thermo, record_id=300
        )
        ensure_record_review(
            db_session, record_type=SubmissionRecordType.kinetics, record_id=301
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=300,
            status=RecordReviewStatus.approved,
            actor=admin,
        )
        only_approved = list_record_reviews(
            db_session, status=RecordReviewStatus.approved, limit=50
        )
        statuses = {(r.record_type, r.record_id) for r in only_approved}
        assert (SubmissionRecordType.thermo, 300) in statuses
        # The kinetics one stayed not_reviewed.
        for r in only_approved:
            assert r.status is RecordReviewStatus.approved


class TestGet:
    def test_missing_returns_none(self, db_session):
        assert (
            get_record_review(
                db_session,
                record_type=SubmissionRecordType.thermo,
                record_id=9999,
            )
            is None
        )
