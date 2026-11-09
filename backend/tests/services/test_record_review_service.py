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

from app.api.errors import DomainError, RecordSelfApprovalForbidden, SubmissionSelfApprovalForbidden
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewEventKind,
    RecordReviewStatus,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
)
from app.db.models.record_review import RecordReview
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.record_review import (
    RecordRef,
    bulk_set_record_review_status,
    ensure_record_review,
    get_record_review,
    list_record_review_events,
    list_record_reviews,
    set_record_review_status,
)
from app.services.submission import approve_submission
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
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
            record_type=SubmissionRecordType.species,
            record_id=12,
            created_by=creator.id,
        )
        with pytest.raises(DomainError, match="record_self_approval"):
            set_record_review_status(
                db_session,
                record_type=SubmissionRecordType.species,
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
            record_type=SubmissionRecordType.species,
            record_id=13,
            created_by=creator.id,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=13,
            status=RecordReviewStatus.approved,
            actor=other,
        )
        # Now the creator deprecates — allowed because only `approved`
        # is gated by the self-review guard.
        row = set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.species,
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
            record_type=SubmissionRecordType.species,
            record_id=14,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=14,
            status=from_status,
            actor=admin,
        )
        with pytest.raises(DomainError, match="Disallowed record-review"):
            set_record_review_status(
                db_session,
                record_type=SubmissionRecordType.species,
                record_id=14,
                status=to_status,
                actor=admin,
            )

    def test_terminal_stamps_reviewer_and_clears_on_return(self, db_session):
        admin = _user(db_session, "admin", AppUserRole.admin)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=15,
        )
        approved = set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.species,
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
            record_type=SubmissionRecordType.species,
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
            RecordRef(SubmissionRecordType.species, 200),
            RecordRef(SubmissionRecordType.species, 201),
            RecordRef(SubmissionRecordType.species, 202),
        ]
        for t in targets:
            ensure_record_review(db_session, record_type=t.record_type, record_id=t.record_id)
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
        ensure_record_review(db_session, record_type=SubmissionRecordType.species, record_id=300)
        ensure_record_review(db_session, record_type=SubmissionRecordType.kinetics, record_id=301)
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=300,
            status=RecordReviewStatus.approved,
            actor=admin,
        )
        only_approved = list_record_reviews(db_session, status=RecordReviewStatus.approved, limit=50)
        statuses = {(r.record_type, r.record_id) for r in only_approved}
        assert (SubmissionRecordType.species, 300) in statuses
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
            record_type=SubmissionRecordType.species,
            record_id=402,
            created_by=creator.id,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=402,
            status=RecordReviewStatus.under_review,
            actor=curator,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=402,
            status=RecordReviewStatus.approved,
            actor=curator,
        )
        events = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.species,
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
            record_type=SubmissionRecordType.species,
            record_id=403,
            status=RecordReviewStatus.approved,
            actor=admin,
        )
        events = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.species,
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
            record_type=SubmissionRecordType.reaction,
            record_id=404,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.reaction,
            record_id=404,
            status=RecordReviewStatus.approved,
            actor=admin,
            note="looks good",
        )
        events = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.reaction,
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
            record_type=SubmissionRecordType.species,
            record_id=405,
            created_by=creator.id,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=405,
            status=RecordReviewStatus.approved,
            actor=curator_a,
        )
        # created + status_change(not_reviewed->approved) so far.
        base = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=405,
        )
        assert len(base) == 2

        # Same-status re-approval by a DIFFERENT curator: reviewer reassigned.
        row = set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=405,
            status=RecordReviewStatus.approved,
            actor=curator_b,
        )
        assert row.reviewed_by == curator_b.id
        after_reassign = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.species,
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
            record_type=SubmissionRecordType.species,
            record_id=405,
            status=RecordReviewStatus.approved,
            actor=curator_b,
        )
        after_noop = list_record_review_events(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=405,
        )
        assert len(after_noop) == 3


class TestSelfApprovalRule:
    """Owned records refuse any depositor; shared identities only a sole depositor."""

    @staticmethod
    def _deposit(session, uploader, record_type, record_id) -> Submission:
        sub = Submission(
            created_by=uploader.id,
            submission_kind=SubmissionKind.conformer,
            source_kind=SubmissionSourceKind.api,
        )
        session.add(sub)
        session.flush()
        session.add(
            SubmissionRecordLink(
                submission_id=sub.id, record_type=record_type, record_id=record_id
            )
        )
        session.flush()
        return sub

    @staticmethod
    def _approve(session, actor, record_type, record_id):
        return set_record_review_status(
            session,
            record_type=record_type,
            record_id=record_id,
            status=RecordReviewStatus.approved,
            actor=actor,
        )

    @staticmethod
    def _entry(session, smiles="[H][H]"):
        species = make_species(session, smiles=smiles)
        return species, make_species_entry(session, species)

    def test_sole_depositor_of_a_species_is_refused(self, db_session):
        depositor = _user(db_session, "dep_species", AppUserRole.curator)
        species, entry = self._entry(db_session)
        self._deposit(db_session, depositor, SubmissionRecordType.species_entry, entry.id)
        assert get_record_review(
            db_session, record_type=SubmissionRecordType.species, record_id=species.id
        ) is None
        with pytest.raises(RecordSelfApprovalForbidden, match="record_self_approval") as caught:
            self._approve(db_session, depositor, SubmissionRecordType.species, species.id)
        assert caught.value.context["record_type"] == "species"
        assert caught.value.context["record_ref"].startswith("spc_")

    def test_other_curator_may_approve_a_species(self, db_session):
        depositor = _user(db_session, "dep_species2", AppUserRole.curator)
        other = _user(db_session, "other_species2", AppUserRole.curator)
        species, entry = self._entry(db_session)
        self._deposit(db_session, depositor, SubmissionRecordType.species_entry, entry.id)
        row = self._approve(db_session, other, SubmissionRecordType.species, species.id)
        assert row.status is RecordReviewStatus.approved

    def test_P1_second_depositor_of_a_shared_entry_may_approve_it(self, db_session):
        first = _user(db_session, "p1_first", AppUserRole.curator)
        second = _user(db_session, "p1_second", AppUserRole.curator)
        _species, entry = self._entry(db_session)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.species_entry,
            record_id=entry.id,
            created_by=first.id,
        )
        self._deposit(db_session, first, SubmissionRecordType.species_entry, entry.id)
        self._deposit(db_session, second, SubmissionRecordType.species_entry, entry.id)
        # Two accounts deposited: neither is the sole depositor, so order is moot.
        row = self._approve(db_session, second, SubmissionRecordType.species_entry, entry.id)
        assert row.status is RecordReviewStatus.approved

    def test_sole_depositor_of_a_shared_entry_is_refused_by_row_creator(self, db_session):
        creator = _user(db_session, "entry_creator", AppUserRole.curator)
        _species, entry = self._entry(db_session)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.species_entry,
            record_id=entry.id,
            created_by=creator.id,
        )
        with pytest.raises(RecordSelfApprovalForbidden):
            self._approve(db_session, creator, SubmissionRecordType.species_entry, entry.id)

    def test_owned_record_is_judged_on_its_creators_not_on_a_citing_link(self, db_session):
        row_creator = _user(db_session, "own_rowcreator", AppUserRole.curator)
        citer = _user(db_session, "own_citer", AppUserRole.curator)
        _species, entry = self._entry(db_session)
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=thermo.id,
            created_by=row_creator.id,
        )
        # The record names its creator, so another account's link only cites it.
        self._deposit(db_session, citer, SubmissionRecordType.thermo, thermo.id)
        with pytest.raises(RecordSelfApprovalForbidden):
            self._approve(db_session, row_creator, SubmissionRecordType.thermo, thermo.id)
        row = self._approve(db_session, citer, SubmissionRecordType.thermo, thermo.id)
        assert row.status is RecordReviewStatus.approved

    def test_Q3_approving_a_submission_that_cites_anothers_record_is_allowed(self, db_session):
        owner = _user(db_session, "q3_owner", AppUserRole.user)
        citer = _user(db_session, "q3_citer", AppUserRole.user)
        approver = _user(db_session, "q3_approver", AppUserRole.curator)
        _species, entry = self._entry(db_session)
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        thermo.created_by = owner.id
        db_session.flush()
        self._deposit(db_session, owner, SubmissionRecordType.thermo, thermo.id)
        cite = self._deposit(db_session, citer, SubmissionRecordType.thermo, thermo.id)
        # The citer, made a curator, may approve what they only cited.
        citer.role = AppUserRole.curator
        db_session.flush()
        approve_submission(db_session, submission_id=cite.id, actor=approver)
        row = self._approve(db_session, citer, SubmissionRecordType.thermo, thermo.id)
        assert row.status is RecordReviewStatus.approved

    def test_restamp_leaves_an_approved_record_untouched(self, db_session):
        depositor_a = _user(db_session, "rs2_a", AppUserRole.curator)
        approver_b = _user(db_session, "rs2_b", AppUserRole.curator)
        uploader_c = _user(db_session, "rs2_c", AppUserRole.user)
        _species, entry = self._entry(db_session)
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        thermo.created_by = depositor_a.id
        db_session.flush()
        self._deposit(db_session, depositor_a, SubmissionRecordType.thermo, thermo.id)
        self._approve(db_session, approver_b, SubmissionRecordType.thermo, thermo.id)
        c_sub = self._deposit(db_session, uploader_c, SubmissionRecordType.thermo, thermo.id)
        events_before = len(
            list_record_review_events(
                db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
            )
        )
        approve_submission(db_session, submission_id=c_sub.id, actor=depositor_a)
        review = get_record_review(
            db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
        )
        assert review.reviewed_by == approver_b.id
        assert len(
            list_record_review_events(
                db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
            )
        ) == events_before

    def test_owned_record_refuses_its_own_created_by_with_no_link(self, db_session):
        depositor = _user(db_session, "dep_owner", AppUserRole.curator)
        _species, entry = self._entry(db_session)
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        thermo.created_by = depositor.id
        db_session.flush()
        with pytest.raises(RecordSelfApprovalForbidden):
            self._approve(db_session, depositor, SubmissionRecordType.thermo, thermo.id)

    def test_P2_first_depositor_may_approve_anothers_submission_over_a_shared_entry(self, db_session):
        first = _user(db_session, "p2_first", AppUserRole.curator)
        second = _user(db_session, "p2_second", AppUserRole.user)
        _species, entry = self._entry(db_session)
        self._deposit(db_session, first, SubmissionRecordType.species_entry, entry.id)
        sub = self._deposit(db_session, second, SubmissionRecordType.species_entry, entry.id)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.species_entry,
            record_id=entry.id,
            created_by=first.id,
            submission_id=sub.id,
        )
        approve_submission(db_session, submission_id=sub.id, actor=first)
        review = get_record_review(
            db_session, record_type=SubmissionRecordType.species_entry, record_id=entry.id
        )
        assert review.status is RecordReviewStatus.approved
        assert review.reviewed_by == first.id

    def test_P2b_depositor_is_refused_an_owned_record_in_a_submission_they_also_linked(self, db_session):
        first = _user(db_session, "p2b_first", AppUserRole.curator)
        other = _user(db_session, "p2b_other", AppUserRole.user)
        _species, entry = self._entry(db_session)
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        sub = self._deposit(db_session, other, SubmissionRecordType.thermo, thermo.id)
        self._deposit(db_session, first, SubmissionRecordType.thermo, thermo.id)
        with pytest.raises(RecordSelfApprovalForbidden):
            approve_submission(db_session, submission_id=sub.id, actor=first)

    def test_P3_bulk_approval_with_a_null_creator_shared_row_is_allowed(self, db_session):
        uploader = _user(db_session, "p3_uploader", AppUserRole.user)
        curator = _user(db_session, "p3_curator", AppUserRole.curator)
        _species, entry = self._entry(db_session)
        sub = self._deposit(db_session, uploader, SubmissionRecordType.species_entry, entry.id)
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.species_entry,
            record_id=entry.id,
            submission_id=sub.id,
        )
        approve_submission(db_session, submission_id=sub.id, actor=curator)
        review = get_record_review(
            db_session, record_type=SubmissionRecordType.species_entry, record_id=entry.id
        )
        assert review.status is RecordReviewStatus.approved

    def test_submission_approval_by_its_uploader_is_a_coded_403_error(self, db_session):
        uploader = _user(db_session, "sub_uploader", AppUserRole.curator)
        _species, entry = self._entry(db_session)
        sub = self._deposit(db_session, uploader, SubmissionRecordType.species_entry, entry.id)
        with pytest.raises(SubmissionSelfApprovalForbidden, match="submission_self_approval"):
            approve_submission(db_session, submission_id=sub.id, actor=uploader)

    def test_bulk_restamp_of_an_already_approved_owned_record_is_not_rejudged(self, db_session):
        depositor = _user(db_session, "rs_dep", AppUserRole.curator)
        approver = _user(db_session, "rs_approver", AppUserRole.curator)
        _species, entry = self._entry(db_session)
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        sub = self._deposit(db_session, depositor, SubmissionRecordType.thermo, thermo.id)
        self._approve(db_session, approver, SubmissionRecordType.thermo, thermo.id)
        # The single-record route still refuses the depositor re-approving...
        with pytest.raises(RecordSelfApprovalForbidden):
            self._approve(db_session, depositor, SubmissionRecordType.thermo, thermo.id)
        # ...but the bulk path re-stamping an approved record does not.
        bulk_set_record_review_status(
            db_session,
            targets=[RecordRef(SubmissionRecordType.thermo, thermo.id)],
            status=RecordReviewStatus.approved,
            actor=depositor,
            submission_id=sub.id,
        )
