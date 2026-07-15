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
    RecordReviewEventKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview
from app.services.record_review import (
    RecordRef,
    bulk_set_record_review_status,
    ensure_record_review,
    get_record_review,
    list_record_review_events,
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


class TestReviewEventHistory:
    def test_ensure_emits_single_created_event(self, db_session):
        alice = _user(db_session, "hist-alice", AppUserRole.user)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=400,
            created_by=alice.id,
        )
        events = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=400,
        )
        assert len(events) == 1
        (created,) = events
        assert created.event_kind is RecordReviewEventKind.created
        assert created.from_status is None
        assert created.to_status is RecordReviewStatus.not_reviewed
        assert created.actor_user_id == alice.id

    def test_ensure_idempotent_does_not_duplicate_event(self, db_session):
        alice = _user(db_session, "hist-idem", AppUserRole.user)
        for _ in range(2):
            ensure_record_review(
                db_session,
                record_type=SubmissionRecordType.kinetics,
                record_id=401,
                created_by=alice.id,
            )
        events = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.kinetics,
            record_id=401,
        )
        assert len(events) == 1

    def test_status_changes_accumulate_in_order(self, db_session):
        creator = _user(db_session, "hist-creator", AppUserRole.user)
        curator = _user(db_session, "hist-curator", AppUserRole.curator)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=402,
            created_by=creator.id,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=402,
            status=RecordReviewStatus.under_review,
            actor=curator,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=402,
            status=RecordReviewStatus.approved,
            actor=curator,
        )
        events = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=402,
        )
        # created + two status_change events, no extras.
        assert len(events) == 3
        kinds = [e.event_kind for e in events]
        assert kinds == [
            RecordReviewEventKind.created,
            RecordReviewEventKind.status_change,
            RecordReviewEventKind.status_change,
        ]
        transitions = [(e.from_status, e.to_status, e.actor_user_id) for e in events]
        assert transitions == [
            (None, RecordReviewStatus.not_reviewed, creator.id),
            (
                RecordReviewStatus.not_reviewed,
                RecordReviewStatus.under_review,
                curator.id,
            ),
            (
                RecordReviewStatus.under_review,
                RecordReviewStatus.approved,
                curator.id,
            ),
        ]

    def test_bootstrap_path_emits_created_then_status_change(self, db_session):
        # set_record_review_status on a record with no existing review row
        # bootstraps a not_reviewed row (created) then transitions it.
        admin = _user(db_session, "hist-admin", AppUserRole.admin)
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=403,
            status=RecordReviewStatus.approved,
            actor=admin,
        )
        events = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=403,
        )
        assert len(events) == 2
        assert events[0].event_kind is RecordReviewEventKind.created
        assert events[0].from_status is None
        assert events[0].to_status is RecordReviewStatus.not_reviewed
        assert events[0].actor_user_id is None
        assert events[1].event_kind is RecordReviewEventKind.status_change
        assert events[1].from_status is RecordReviewStatus.not_reviewed
        assert events[1].to_status is RecordReviewStatus.approved
        assert events[1].actor_user_id == admin.id

    def test_reason_note_is_recorded_on_status_change(self, db_session):
        admin = _user(db_session, "hist-note", AppUserRole.admin)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.kinetics,
            record_id=404,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.kinetics,
            record_id=404,
            status=RecordReviewStatus.approved,
            actor=admin,
            note="looks good",
        )
        events = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.kinetics,
            record_id=404,
        )
        assert len(events) == 2
        assert events[-1].reason == "looks good"

    def test_list_events_empty_when_no_review_row(self, db_session):
        assert (
            list_record_review_events(
                db_session,
                record_type=SubmissionRecordType.thermo,
                record_id=8888,
            )
            == []
        )

    def test_terminal_selfloop_reassignment_emits_event(self, db_session):
        # A same-status terminal transition that reassigns the reviewer
        # (curator B re-approving a record approved by curator A) must be
        # recorded — reviewed_by changes with no status change, and that is
        # exactly the who/when this history exists to capture. A true no-op
        # (same actor, nothing changed) must NOT add an event.
        creator = _user(db_session, "sl-creator", AppUserRole.user)
        curator_a = _user(db_session, "sl-curator-a", AppUserRole.curator)
        curator_b = _user(db_session, "sl-curator-b", AppUserRole.curator)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=405,
            created_by=creator.id,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=405,
            status=RecordReviewStatus.approved,
            actor=curator_a,
        )
        # created + status_change(not_reviewed->approved) so far.
        base = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=405,
        )
        assert len(base) == 2

        # Same-status re-approval by a DIFFERENT curator: reviewer reassigned.
        row = set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=405,
            status=RecordReviewStatus.approved,
            actor=curator_b,
        )
        assert row.reviewed_by == curator_b.id
        after_reassign = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=405,
        )
        assert len(after_reassign) == 3
        last = after_reassign[-1]
        assert last.event_kind is RecordReviewEventKind.status_change
        assert last.from_status is RecordReviewStatus.approved
        assert last.to_status is RecordReviewStatus.approved
        assert last.actor_user_id == curator_b.id

        # True no-op: same actor re-approves, reviewed_by unchanged -> no event.
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=405,
            status=RecordReviewStatus.approved,
            actor=curator_b,
        )
        after_noop = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=405,
        )
        assert len(after_noop) == 3
