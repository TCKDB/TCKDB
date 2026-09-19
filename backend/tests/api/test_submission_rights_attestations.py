"""``POST`` / ``GET /api/v1/submissions/{id}/rights-attestations`` over the wire.

Who may say what: a ``depositor_agreement`` only from the account that made
the deposit; ``historical_review`` and the other curator bases only from a
curator or admin. The chain is append-only and read back oldest first, and
no error body names a row id.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from app.db.models.app_user import AppUser
from app.db.models.submission_rights import SubmissionRightsAttestation

_THERMO = {
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "scientific_origin": "computed",
    "h298_kj_mol": 217.998,
}


def _deposit_without_rights(client) -> int:
    """An ordinary upload by the default test user, no ``rights`` fragment."""
    resp = client.post("/api/v1/uploads/thermo", json=_THERMO)
    assert resp.status_code == 201, resp.text
    return resp.json()["submission_id"]


def _url(submission_id: int) -> str:
    return f"/api/v1/submissions/{submission_id}/rights-attestations"


def _assert_no_ids_in(detail: str, *ids: int) -> None:
    for row_id in ids:
        assert not re.search(rf"\b{row_id}\b", detail), detail


def test_a_curator_records_a_historical_review(
    client, login_as, _api_curator_user, db_session
):
    submission_id = _deposit_without_rights(client)
    login_as(_api_curator_user)

    created = client.post(
        _url(submission_id),
        json={
            "license": "CC-BY-4.0",
            "basis": "historical_review",
            "note": "Reviewed with the depositor by email, 2026-09-12.",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["attestation_ref"].startswith("sra_")
    assert body["submission_ref"].startswith("sub_")
    assert body["basis"] == "historical_review"
    assert body["actor_kind"] == "curator"
    assert body["license"] == "CC-BY-4.0"
    assert body["stands"] is True
    assert body["supersedes_attestation_ref"] is None
    assert body["attested_by"]["username"] == "testcurator"
    # Refs only: no row id of any kind in the response (``stands`` is a bool,
    # and a bool is an int to ``isinstance``; it is not an id).
    assert not any(isinstance(v, int) and not isinstance(v, bool) for v in body.values())

    row = db_session.scalar(
        select(SubmissionRightsAttestation).where(
            SubmissionRightsAttestation.public_ref == body["attestation_ref"]
        )
    )
    assert row is not None and row.attested_by == _api_curator_user


def test_a_depositor_cannot_claim_a_historical_review(client, _api_test_user):
    submission_id = _deposit_without_rights(client)
    refused = client.post(
        _url(submission_id), json={"license": "CC-BY-4.0", "basis": "historical_review"}
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "rights_attestation_requires_curator"
    _assert_no_ids_in(refused.json()["detail"], submission_id, _api_test_user)


def test_a_non_owner_cannot_claim_a_depositor_agreement(
    client, login_as, _api_curator_user, _api_other_user, _api_test_user
):
    submission_id = _deposit_without_rights(client)

    # A curator who did not make the deposit: may see it, may not agree for it.
    login_as(_api_curator_user)
    refused = client.post(
        _url(submission_id), json={"license": "CC-BY-4.0", "basis": "depositor_agreement"}
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "rights_attestation_not_depositor"
    _assert_no_ids_in(refused.json()["detail"], submission_id, _api_test_user, _api_curator_user)

    # Another ordinary user: cannot even see the submission.
    login_as(_api_other_user)
    hidden = client.post(
        _url(submission_id), json={"license": "CC-BY-4.0", "basis": "depositor_agreement"}
    )
    assert hidden.status_code == 403, hidden.text
    _assert_no_ids_in(hidden.json()["detail"], submission_id, _api_test_user, _api_other_user)
    assert client.get(_url(submission_id)).status_code == 403


def test_the_list_is_the_chain_oldest_first(
    client, login_as, _api_curator_user, _api_test_user
):
    submission_id = _deposit_without_rights(client)

    first = client.post(
        _url(submission_id), json={"license": "CC0-1.0", "basis": "depositor_agreement"}
    )
    assert first.status_code == 201, first.text
    login_as(_api_curator_user)
    second = client.post(
        _url(submission_id),
        json={
            "license": "CC-BY-4.0",
            "basis": "source_terms",
            "source_terms": "Deposited from the group's own 2025 dataset, CC BY 4.0.",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["supersedes_attestation_ref"] == first.json()["attestation_ref"]

    listed = client.get(_url(submission_id))
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [r["attestation_ref"] for r in rows] == [
        first.json()["attestation_ref"],
        second.json()["attestation_ref"],
    ]
    assert rows[0]["stands"] is False
    assert rows[0]["superseded_by_attestation_ref"] == rows[1]["attestation_ref"]
    assert rows[1]["stands"] is True
    assert rows[1]["source_terms"].startswith("Deposited from")

    # The depositor sees their own chain too.
    login_as(_api_test_user)
    assert len(client.get(_url(submission_id)).json()) == 2


def test_source_terms_are_required_for_that_basis(client, login_as, _api_curator_user):
    submission_id = _deposit_without_rights(client)
    login_as(_api_curator_user)
    refused = client.post(
        _url(submission_id), json={"license": "CC-BY-4.0", "basis": "source_terms"}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "rights_source_terms_required"
    _assert_no_ids_in(refused.json()["detail"], submission_id)


def test_an_unknown_submission_is_a_404_without_its_id(client):
    missing = 987654321
    refused = client.get(_url(missing))
    assert refused.status_code == 404, refused.text
    _assert_no_ids_in(refused.json()["detail"], missing)


def test_the_curator_label_never_carries_the_user_row(client, login_as, _api_curator_user, db_session):
    submission_id = _deposit_without_rights(client)
    login_as(_api_curator_user)
    created = client.post(
        _url(submission_id), json={"license": "CC-BY-4.0", "basis": "operator_own_data"}
    ).json()
    curator = db_session.get(AppUser, _api_curator_user)
    assert created["attested_by"] == {
        "username": curator.username,
        "full_name": curator.full_name,
        "orcid": curator.orcid,
        "affiliation": curator.affiliation,
    }
