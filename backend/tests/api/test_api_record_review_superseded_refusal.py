"""``PATCH /record-reviews/{type}/{id}`` refuses re-approving a superseded record.

The refusal is a 409 with the coded ``superseded_record_not_approvable``. A
handler that was not registered would answer 400 ``domain_error`` instead, since
the exception is a ``DomainError`` subclass.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.network import Network
from app.db.models.record_review import RecordReview
from app.db.models.submission import SubmissionAuditEvent
from app.services.record_review import ensure_record_review, set_record_review_status
from app.services.scientific_record_supersession import supersede_scientific_record
from app.services.submission import create_submission, link_record


def _approved_network(session, name: str, actor: AppUser) -> Network:
    row = Network(name=name)
    session.add(row)
    session.flush()
    ensure_record_review(session, record_type=SubmissionRecordType.network, record_id=row.id)
    set_record_review_status(
        session,
        record_type=SubmissionRecordType.network,
        record_id=row.id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )
    return row


def _superseded_pair(session, actor: AppUser) -> tuple[Network, Network]:
    old = _approved_network(session, "api-old", actor)
    new = _approved_network(session, "api-new", actor)
    supersede_scientific_record(
        session,
        record_type=SubmissionRecordType.network,
        superseded_record_id=old.id,
        superseding_record_id=new.id,
        actor=actor,
        reason="corrected topology",
    )
    return old, new


def test_patch_reapproval_of_a_superseded_record_is_409_with_the_code(
    client, db_session, login_as, _api_curator_user
) -> None:
    actor = login_as(_api_curator_user)
    old, new = _superseded_pair(db_session, actor)

    resp = client.patch(f"/api/v1/record-reviews/network/{old.id}", json={"status": "approved"})

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "superseded_record_not_approvable"
    assert body["context"] == {
        "record_type": "network",
        "record_ref": old.public_ref,
        "superseded_by_ref": new.public_ref,
    }
    # Public refs only: neither row id appears anywhere in the body.
    assert f'"{old.id}"' not in resp.text and f": {old.id}" not in resp.text


def test_patch_other_transitions_and_the_successor_are_unaffected(
    client, db_session, login_as, _api_curator_user
) -> None:
    actor = login_as(_api_curator_user)
    old, new = _superseded_pair(db_session, actor)

    reopened = client.patch(f"/api/v1/record-reviews/network/{old.id}", json={"status": "under_review"})
    assert reopened.status_code == 200, reopened.text
    rejected = client.patch(f"/api/v1/record-reviews/network/{old.id}", json={"status": "rejected"})
    assert rejected.status_code == 200, rejected.text

    # Approving the superseding record (already approved, by the same curator) is not refused.
    other = AppUser(username="api-curator-2", role=AppUserRole.curator)
    db_session.add(other)
    db_session.flush()
    login_as(other.id)
    ok = client.patch(f"/api/v1/record-reviews/network/{new.id}", json={"status": "approved"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"


def test_submission_approval_skips_a_superseded_member_and_stays_approvable(
    client, db_session, login_as, _api_curator_user, _api_test_user
) -> None:
    """The review queue approves records one at a time, so a submission can hold a
    member that a correction has already replaced. Approving the submission must
    still succeed: the replaced member stays deprecated, the rest are approved,
    and the skipped member is listed by public ref in the audit event."""
    actor = login_as(_api_curator_user)
    old, new = _superseded_pair(db_session, actor)
    fresh = Network(name="api-fresh")
    db_session.add(fresh)
    db_session.flush()
    ensure_record_review(db_session, record_type=SubmissionRecordType.network, record_id=fresh.id)

    sub = create_submission(
        db_session, created_by=_api_test_user, submission_kind=SubmissionKind.thermo, title="mixed"
    )
    db_session.flush()
    for row in (old, new, fresh):
        link_record(db_session, submission=sub, record_type=SubmissionRecordType.network, record_id=row.id)
    db_session.flush()

    resp = client.post(f"/api/v1/submissions/{sub.id}/approve")

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    status = {
        r.record_id: r.status
        for r in db_session.scalars(
            select(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.network,
                RecordReview.record_id.in_([old.id, new.id, fresh.id]),
            )
        )
    }
    assert status[old.id] is RecordReviewStatus.deprecated
    assert status[new.id] is RecordReviewStatus.approved
    assert status[fresh.id] is RecordReviewStatus.approved

    event = db_session.scalar(
        select(SubmissionAuditEvent).where(
            SubmissionAuditEvent.submission_id == sub.id,
            SubmissionAuditEvent.event_kind == SubmissionAuditEventKind.curator_approved,
        )
    )
    assert event.details_json == {
        "skipped_superseded_records": [
            {"record_type": "network", "record_ref": old.public_ref, "superseded_by_ref": new.public_ref}
        ]
    }
