"""``PATCH /record-reviews/{type}/{id}`` refuses re-approving a superseded record.

The refusal is a 409 with the coded ``superseded_record_not_approvable``. A
handler that was not registered would answer 400 ``domain_error`` instead, since
the exception is a ``DomainError`` subclass.
"""

from __future__ import annotations

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.network import Network
from app.services.record_review import ensure_record_review, set_record_review_status
from app.services.scientific_record_supersession import supersede_scientific_record


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
