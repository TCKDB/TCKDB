"""API coverage for curator reproducibility and supersession operations."""

from __future__ import annotations

from app.db.models.app_user import AppUser
from app.db.models.common import RecordReviewStatus, ReproducibilityGrade, SubmissionRecordType
from app.db.models.network import Network
from app.db.models.reaction import ChemReaction
from app.services.record_review import ensure_record_review, set_record_review_status


def _assessment_url(record_type: str, record_id: int) -> str:
    return f"/api/v1/curation/reproducibility-assessments/{record_type}/{record_id}"


def _approved_network(db_session, *, name: str, actor: AppUser) -> Network:
    network = Network(name=name, created_by=actor.id)
    db_session.add(network)
    db_session.flush()
    ensure_record_review(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=network.id,
    )
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=network.id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )
    return network


def test_evaluation_is_curator_gated_and_server_derived(
    client,
    db_session,
    login_as,
    _api_curator_user,
) -> None:
    reaction = ChemReaction(reversible=True)
    db_session.add(reaction)
    db_session.flush()
    url = _assessment_url("reaction", reaction.id) + "/evaluate"

    assert client.post(url).status_code == 403
    login_as(_api_curator_user)
    response = client.post(url)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["grade"] == ReproducibilityGrade.insufficient.value
    assert body["rubric_name"] == "tckdb_reproducibility"
    assert body["rubric_version"] == "v1"
    assert body["assessor_kind"] == "system"
    assert body["assessor_user_id"] is None


def test_latest_assessment_read_is_curator_gated(
    client,
    db_session,
    login_as,
    _api_curator_user,
) -> None:
    reaction = ChemReaction(reversible=True)
    db_session.add(reaction)
    db_session.flush()
    base = _assessment_url("reaction", reaction.id)
    login_as(_api_curator_user)

    assert client.get(base + "/latest").status_code == 404
    created = client.post(base + "/evaluate")
    latest = client.get(base + "/latest")

    assert created.status_code == 201
    assert latest.status_code == 200
    assert latest.json()["id"] == created.json()["id"]


def test_curator_can_supersede_approved_same_subject_records(
    client,
    db_session,
    login_as,
    _api_curator_user,
) -> None:
    actor = db_session.get(AppUser, _api_curator_user)
    old = _approved_network(db_session, name="same subject", actor=actor)
    new = _approved_network(db_session, name="same subject", actor=actor)
    login_as(_api_curator_user)

    response = client.post(
        "/api/v1/curation/scientific-record-supersessions",
        json={
            "record_type": "network",
            "superseded_record_id": old.id,
            "superseding_record_id": new.id,
            "reason": "corrected network evidence",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == _api_curator_user
    assert response.json()["superseded_record_id"] == old.id
