"""Service-layer tests for submission moderation lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.api.errors import DomainError, NotFoundError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionPrecheckLabel,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.services.submission import (
    append_audit_event,
    approve_submission,
    create_submission,
    link_record,
    link_records,
    list_audit_events,
    list_my_submissions,
    list_record_links,
    list_submissions_for_review,
    mark_ingestion_failed,
    mark_ingestion_succeeded,
    mark_precheck_result,
    reject_submission,
    supersede_submission,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_user(session, username: str, role: AppUserRole = AppUserRole.user) -> AppUser:
    user = AppUser(username=username, role=role)
    session.add(user)
    session.flush()
    return user


def _uploader(session, name: str = "alice") -> AppUser:
    return _make_user(session, name, AppUserRole.user)


def _curator(session, name: str = "curator-one") -> AppUser:
    return _make_user(session, name, AppUserRole.curator)


def _open_pending(session, uploader_id: int) -> Submission:
    return create_submission(
        session,
        created_by=uploader_id,
        submission_kind=SubmissionKind.thermo,
        title="test upload",
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


class TestCreateSubmission:
    def test_persists_pending_row_and_audit_event(self, db_session):
        alice = _uploader(db_session)

        submission = create_submission(
            db_session,
            created_by=alice.id,
            submission_kind=SubmissionKind.conformer,
            source_kind=SubmissionSourceKind.api,
            title="conformer upload",
            summary="imported from ARC",
        )

        assert submission.id is not None
        assert submission.status is SubmissionStatus.pending
        assert submission.created_by == alice.id
        assert submission.source_kind is SubmissionSourceKind.api
        assert submission.submission_kind is SubmissionKind.conformer
        assert submission.is_public is False

        events = list_audit_events(db_session, submission_id=submission.id)
        assert len(events) == 1
        assert events[0].event_kind is SubmissionAuditEventKind.submission_created
        assert events[0].actor_kind is SubmissionActorKind.user
        assert events[0].actor_user_id == alice.id
        assert events[0].to_status is SubmissionStatus.pending

    def test_rejects_missing_uploader(self, db_session):
        with pytest.raises(NotFoundError):
            create_submission(
                db_session,
                created_by=9_999_999,
                submission_kind=SubmissionKind.thermo,
            )

    def test_supersedes_link_requires_existing_target(self, db_session):
        alice = _uploader(db_session)
        with pytest.raises(NotFoundError):
            create_submission(
                db_session,
                created_by=alice.id,
                submission_kind=SubmissionKind.thermo,
                supersedes_submission_id=9_999_999,
            )


# ---------------------------------------------------------------------------
# Precheck
# ---------------------------------------------------------------------------


class TestPrecheck:
    def test_passed_advances_to_precheck_passed(self, db_session):
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)

        updated = mark_precheck_result(
            db_session,
            submission_id=sub.id,
            label=SubmissionPrecheckLabel.passed,
            model="gpt-triage-v1",
            summary="no obvious issues",
        )
        assert updated.status is SubmissionStatus.precheck_passed
        assert updated.llm_precheck_label is SubmissionPrecheckLabel.passed
        assert updated.llm_precheck_model == "gpt-triage-v1"
        assert updated.llm_precheck_at is not None

        events = list_audit_events(db_session, submission_id=sub.id)
        precheck_events = [
            e for e in events
            if e.event_kind is SubmissionAuditEventKind.llm_precheck_passed
        ]
        assert len(precheck_events) == 1
        assert precheck_events[0].actor_kind is SubmissionActorKind.llm
        assert precheck_events[0].actor_user_id is None

    def test_flagged_advances_to_auto_flagged(self, db_session):
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)

        updated = mark_precheck_result(
            db_session,
            submission_id=sub.id,
            label=SubmissionPrecheckLabel.flagged,
        )
        assert updated.status is SubmissionStatus.auto_flagged

    def test_precheck_does_not_count_as_human_approval(self, db_session):
        """LLM precheck must never set ``approved`` or record a human actor."""
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)
        mark_precheck_result(
            db_session,
            submission_id=sub.id,
            label=SubmissionPrecheckLabel.passed,
        )
        refreshed = db_session.get(Submission, sub.id)
        assert refreshed.status is not SubmissionStatus.approved
        assert refreshed.approved_at is None
        assert refreshed.approved_by is None

    def test_cannot_run_precheck_after_curator_approval(self, db_session):
        alice = _uploader(db_session)
        curator = _curator(db_session)
        sub = _open_pending(db_session, alice.id)
        approve_submission(db_session, submission_id=sub.id, actor=curator)

        with pytest.raises(DomainError):
            mark_precheck_result(
                db_session,
                submission_id=sub.id,
                label=SubmissionPrecheckLabel.passed,
            )


# ---------------------------------------------------------------------------
# Curator approval / rejection
# ---------------------------------------------------------------------------


class TestApproval:
    def test_curator_can_approve_pending_submission(self, db_session):
        alice = _uploader(db_session)
        curator = _curator(db_session)
        sub = _open_pending(db_session, alice.id)

        updated = approve_submission(
            db_session, submission_id=sub.id, actor=curator
        )
        assert updated.status is SubmissionStatus.approved
        assert updated.approved_by == curator.id
        assert updated.approved_at is not None
        assert updated.is_public is True

        events = list_audit_events(db_session, submission_id=sub.id)
        approval = [
            e for e in events
            if e.event_kind is SubmissionAuditEventKind.curator_approved
        ]
        assert len(approval) == 1
        assert approval[0].actor_kind is SubmissionActorKind.curator
        assert approval[0].actor_user_id == curator.id
        assert approval[0].from_status is SubmissionStatus.pending
        assert approval[0].to_status is SubmissionStatus.approved

    def test_non_curator_cannot_approve(self, db_session):
        alice = _uploader(db_session)
        bob = _uploader(db_session, "bob")
        sub = _open_pending(db_session, alice.id)

        with pytest.raises(DomainError):
            approve_submission(db_session, submission_id=sub.id, actor=bob)

    def test_uploader_cannot_approve_own_submission(self, db_session):
        """Self-approval is disallowed even if the uploader is a curator."""
        alice_curator = _make_user(db_session, "alice-curator", AppUserRole.curator)
        sub = _open_pending(db_session, alice_curator.id)

        with pytest.raises(DomainError):
            approve_submission(
                db_session, submission_id=sub.id, actor=alice_curator
            )

    def test_can_approve_from_precheck_passed(self, db_session):
        alice = _uploader(db_session)
        curator = _curator(db_session)
        sub = _open_pending(db_session, alice.id)
        mark_precheck_result(
            db_session,
            submission_id=sub.id,
            label=SubmissionPrecheckLabel.passed,
        )

        approved = approve_submission(
            db_session, submission_id=sub.id, actor=curator
        )
        assert approved.status is SubmissionStatus.approved

    def test_cannot_reapprove_already_approved(self, db_session):
        alice = _uploader(db_session)
        curator = _curator(db_session)
        sub = _open_pending(db_session, alice.id)
        approve_submission(db_session, submission_id=sub.id, actor=curator)

        with pytest.raises(DomainError):
            approve_submission(db_session, submission_id=sub.id, actor=curator)


class TestRejection:
    def test_curator_can_reject_with_reason(self, db_session):
        alice = _uploader(db_session)
        curator = _curator(db_session)
        sub = _open_pending(db_session, alice.id)

        updated = reject_submission(
            db_session,
            submission_id=sub.id,
            actor=curator,
            reason="missing frequencies",
        )
        assert updated.status is SubmissionStatus.rejected
        assert updated.rejected_by == curator.id
        assert updated.rejection_reason == "missing frequencies"
        assert updated.rejected_at is not None

        events = list_audit_events(db_session, submission_id=sub.id)
        rej = [
            e for e in events
            if e.event_kind is SubmissionAuditEventKind.curator_rejected
        ]
        assert len(rej) == 1
        assert rej[0].reason == "missing frequencies"

    def test_rejection_without_reason_is_rejected(self, db_session):
        alice = _uploader(db_session)
        curator = _curator(db_session)
        sub = _open_pending(db_session, alice.id)

        with pytest.raises(DomainError):
            reject_submission(
                db_session, submission_id=sub.id, actor=curator, reason="   "
            )

    def test_correction_window_emits_extra_audit_event(self, db_session):
        alice = _uploader(db_session)
        curator = _curator(db_session)
        sub = _open_pending(db_session, alice.id)

        due = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7)
        reject_submission(
            db_session,
            submission_id=sub.id,
            actor=curator,
            reason="please rerun at CCSD(T)",
            correction_due_at=due,
        )

        events = list_audit_events(db_session, submission_id=sub.id)
        kinds = [e.event_kind for e in events]
        assert SubmissionAuditEventKind.curator_rejected in kinds
        assert SubmissionAuditEventKind.correction_window_opened in kinds

    def test_uploader_cannot_reject_own_submission(self, db_session):
        alice_curator = _make_user(db_session, "alice-2", AppUserRole.curator)
        sub = _open_pending(db_session, alice_curator.id)

        with pytest.raises(DomainError):
            reject_submission(
                db_session,
                submission_id=sub.id,
                actor=alice_curator,
                reason="bad data",
            )


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------


class TestSupersede:
    def test_replaces_rejected_submission_without_deletion(self, db_session):
        alice = _uploader(db_session)
        curator = _curator(db_session)
        old = _open_pending(db_session, alice.id)
        reject_submission(
            db_session,
            submission_id=old.id,
            actor=curator,
            reason="wrong level of theory",
        )

        new = create_submission(
            db_session,
            created_by=alice.id,
            submission_kind=SubmissionKind.thermo,
            supersedes_submission_id=old.id,
        )
        supersede_submission(
            db_session,
            old_submission_id=old.id,
            new_submission_id=new.id,
            actor=alice,
        )

        old_row = db_session.get(Submission, old.id)
        assert old_row is not None
        assert old_row.status is SubmissionStatus.superseded
        # Prior rejection state is preserved; it is not hard-deleted
        assert old_row.rejected_by == curator.id
        assert old_row.rejection_reason == "wrong level of theory"

        new_row = db_session.get(Submission, new.id)
        assert new_row.supersedes_submission_id == old.id

        old_events = list_audit_events(db_session, submission_id=old.id)
        assert any(
            e.event_kind is SubmissionAuditEventKind.submission_superseded
            and e.related_submission_id == new.id
            for e in old_events
        )
        new_events = list_audit_events(db_session, submission_id=new.id)
        assert any(
            e.event_kind is SubmissionAuditEventKind.correction_uploaded
            and e.related_submission_id == old.id
            for e in new_events
        )

    def test_supersede_is_idempotent(self, db_session):
        alice = _uploader(db_session)
        old = _open_pending(db_session, alice.id)
        new = create_submission(
            db_session,
            created_by=alice.id,
            submission_kind=SubmissionKind.thermo,
            supersedes_submission_id=old.id,
        )
        supersede_submission(
            db_session, old_submission_id=old.id, new_submission_id=new.id
        )
        supersede_submission(
            db_session, old_submission_id=old.id, new_submission_id=new.id
        )
        # Only one superseded event on the old side
        events = list_audit_events(db_session, submission_id=old.id)
        super_events = [
            e for e in events
            if e.event_kind is SubmissionAuditEventKind.submission_superseded
        ]
        assert len(super_events) == 1

    def test_supersede_requires_linked_supersedes_id(self, db_session):
        alice = _uploader(db_session)
        old = _open_pending(db_session, alice.id)
        unlinked_new = _open_pending(db_session, alice.id)

        with pytest.raises(DomainError):
            supersede_submission(
                db_session,
                old_submission_id=old.id,
                new_submission_id=unlinked_new.id,
            )

    def test_self_supersede_rejected(self, db_session):
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)
        with pytest.raises(DomainError):
            supersede_submission(
                db_session,
                old_submission_id=sub.id,
                new_submission_id=sub.id,
            )


# ---------------------------------------------------------------------------
# Record linkage
# ---------------------------------------------------------------------------


class TestRecordLinkage:
    def test_link_record_is_idempotent(self, db_session):
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)
        a = link_record(
            db_session,
            submission=sub,
            record_type=SubmissionRecordType.thermo,
            record_id=42,
            role="primary",
        )
        b = link_record(
            db_session,
            submission=sub,
            record_type=SubmissionRecordType.thermo,
            record_id=42,
            role="primary",
        )
        assert a.id == b.id

    def test_role_distinguishes_links(self, db_session):
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)
        a = link_record(
            db_session,
            submission=sub,
            record_type=SubmissionRecordType.calculation,
            record_id=7,
            role="opt",
        )
        b = link_record(
            db_session,
            submission=sub,
            record_type=SubmissionRecordType.calculation,
            record_id=7,
            role="freq",
        )
        assert a.id != b.id

    def test_bulk_link_records(self, db_session):
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)
        links = link_records(
            db_session,
            submission=sub,
            records=[
                (SubmissionRecordType.species_entry, 1, None),
                (SubmissionRecordType.thermo, 2, "primary"),
                (SubmissionRecordType.thermo, 2, "primary"),  # duplicate
            ],
        )
        assert len({link.id for link in links}) == 2

        rows = list_record_links(db_session, submission_id=sub.id)
        assert {(r.record_type, r.record_id, r.role) for r in rows} == {
            (SubmissionRecordType.species_entry, 1, None),
            (SubmissionRecordType.thermo, 2, "primary"),
        }


# ---------------------------------------------------------------------------
# Ingestion events
# ---------------------------------------------------------------------------


class TestIngestionEvents:
    def test_success_event_does_not_change_status(self, db_session):
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)
        mark_ingestion_succeeded(
            db_session, submission=sub, summary="5 records created"
        )
        assert sub.status is SubmissionStatus.pending
        events = list_audit_events(db_session, submission_id=sub.id)
        assert any(
            e.event_kind is SubmissionAuditEventKind.ingestion_succeeded
            and e.actor_kind is SubmissionActorKind.system
            for e in events
        )

    def test_ingestion_failed_records_reason(self, db_session):
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)
        mark_ingestion_failed(
            db_session,
            submission=sub,
            reason="parser raised on malformed xyz",
        )
        events = list_audit_events(db_session, submission_id=sub.id)
        failed = [
            e for e in events
            if e.event_kind is SubmissionAuditEventKind.ingestion_failed
        ]
        assert len(failed) == 1
        assert failed[0].reason == "parser raised on malformed xyz"


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestListing:
    def test_list_my_submissions(self, db_session):
        alice = _uploader(db_session, "alice-list")
        bob = _uploader(db_session, "bob-list")
        s1 = _open_pending(db_session, alice.id)
        s2 = _open_pending(db_session, alice.id)
        _open_pending(db_session, bob.id)

        rows = list_my_submissions(db_session, user_id=alice.id)
        assert {r.id for r in rows} == {s1.id, s2.id}

    def test_list_submissions_for_review_default_scope(self, db_session):
        alice = _uploader(db_session, "alice-review")
        curator = _curator(db_session, "curator-review")
        pending = _open_pending(db_session, alice.id)
        approved = _open_pending(db_session, alice.id)
        approve_submission(
            db_session, submission_id=approved.id, actor=curator
        )

        rows = list_submissions_for_review(db_session)
        ids = {r.id for r in rows}
        assert pending.id in ids
        assert approved.id not in ids


# ---------------------------------------------------------------------------
# Append-only invariant
# ---------------------------------------------------------------------------


class TestAuditAppendOnly:
    def test_existing_events_remain_unchanged_through_lifecycle(self, db_session):
        alice = _uploader(db_session)
        curator = _curator(db_session)
        sub = _open_pending(db_session, alice.id)

        initial_events = list_audit_events(db_session, submission_id=sub.id)
        initial_snapshot = [
            (e.id, e.event_kind, e.from_status, e.to_status)
            for e in initial_events
        ]

        mark_precheck_result(
            db_session,
            submission_id=sub.id,
            label=SubmissionPrecheckLabel.passed,
        )
        approve_submission(
            db_session, submission_id=sub.id, actor=curator
        )

        after_events = list_audit_events(db_session, submission_id=sub.id)

        # initial_events' rows are still intact, just appended to
        for snap in initial_snapshot:
            event_id, kind, from_s, to_s = snap
            match = next((e for e in after_events if e.id == event_id), None)
            assert match is not None
            assert match.event_kind is kind
            assert match.from_status == from_s
            assert match.to_status == to_s

        assert len(after_events) > len(initial_events)


# ---------------------------------------------------------------------------
# append_audit_event plumbing
# ---------------------------------------------------------------------------


class TestAppendAuditEvent:
    def test_custom_event_with_details_json(self, db_session):
        alice = _uploader(db_session)
        sub = _open_pending(db_session, alice.id)
        event = append_audit_event(
            db_session,
            submission=sub,
            event_kind=SubmissionAuditEventKind.public_visibility_changed,
            actor_kind=SubmissionActorKind.system,
            details_json={"is_public": True},
        )
        assert event.details_json == {"is_public": True}
        assert event.actor_kind is SubmissionActorKind.system
