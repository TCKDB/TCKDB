"""API tests for the hosted contribution-bundle submit/import endpoint.

POST /api/v1/bundles/submit

Covers auth (anonymous/API key/invalid key), thermo + kinetics happy
paths, the dry-run blocking gate, transaction rollback behaviour,
unreviewed/pending-review state on imported rows, and verifies that
local exporter metadata never becomes hosted actor identity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.app import create_app
from app.api.deps import get_current_user, get_db, get_write_db
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    SubmissionAuditEventKind,
    SubmissionRecordType,
    SubmissionStatus,
)
from app.db.models.kinetics import Kinetics
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.db.models.thermo import Thermo


REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"
ENDPOINT = "/api/v1/bundles/submit"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def raw_client(db_session) -> Iterator[TestClient]:
    """TestClient WITHOUT the get_current_user override.

    Mirrors tests/api/test_api_bundle_dry_run.py — needed for the
    anonymous and bad-API-key tests, which would otherwise be silently
    authenticated by the default ``client`` fixture.
    """
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def _load_bundle(filename: str) -> dict:
    return json.loads((EXAMPLES_DIR / filename).read_text())


def _counts(session) -> dict[str, int]:
    return {
        "thermo": session.scalar(select(func.count()).select_from(Thermo)) or 0,
        "kinetics": session.scalar(select(func.count()).select_from(Kinetics))
        or 0,
        "submission": session.scalar(select(func.count()).select_from(Submission))
        or 0,
        "audit": session.scalar(
            select(func.count()).select_from(SubmissionAuditEvent)
        )
        or 0,
        "links": session.scalar(
            select(func.count()).select_from(SubmissionRecordLink)
        )
        or 0,
        "species": session.scalar(select(func.count()).select_from(Species))
        or 0,
    }


# ---------------------------------------------------------------------------
# Auth tests (use raw_client — real auth dependency)
# ---------------------------------------------------------------------------


def test_anonymous_submit_rejected(raw_client, db_session) -> None:
    bundle = _load_bundle("thermo-bundle-v0.json")
    before = _counts(db_session)
    resp = raw_client.post(ENDPOINT, json=bundle)
    after = _counts(db_session)

    assert resp.status_code == 401
    assert before == after


def test_invalid_api_key_rejected(raw_client, db_session) -> None:
    bundle = _load_bundle("thermo-bundle-v0.json")
    before = _counts(db_session)
    resp = raw_client.post(
        ENDPOINT, json=bundle, headers={"X-API-Key": "definitely-not-valid"}
    )
    after = _counts(db_session)

    assert resp.status_code == 401
    assert before == after


def test_valid_api_key_submit_imports_with_owner_as_actor(
    raw_client, db_session
) -> None:
    """Submit with the seeded session API key.

    The session-scoped fixture in tests/conftest.py creates a user
    'testuser' and an API key 'test-api-key-for-tckdb'. That user must
    end up as ``submission.created_by`` and as ``thermo.created_by``.
    """
    bundle = _load_bundle("thermo-bundle-v0.json")
    resp = raw_client.post(
        ENDPOINT,
        json=bundle,
        headers={"X-API-Key": "test-api-key-for-tckdb"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    api_key_owner = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    assert api_key_owner is not None

    submission = db_session.get(Submission, body["submission_id"])
    assert submission.created_by == api_key_owner.id

    thermo_id = next(
        r["record_id"] for r in body["records"] if r["record_type"] == "thermo"
    )
    thermo = db_session.get(Thermo, thermo_id)
    assert thermo.created_by == api_key_owner.id


# ---------------------------------------------------------------------------
# Happy-path tests (use default client — auth pre-overridden)
# ---------------------------------------------------------------------------


def test_valid_thermo_bundle_imports(client, db_session) -> None:
    bundle = _load_bundle("thermo-bundle-v0.json")

    before = _counts(db_session)
    resp = client.post(ENDPOINT, json=bundle)
    after = _counts(db_session)

    assert resp.status_code == 201, resp.text
    body = resp.json()

    # response shape
    assert body["bundle_kind"] == "thermo"
    assert body["status"] == SubmissionStatus.pending.value
    assert body["review_status"] == "unreviewed"
    assert body["summary"]["records_imported"] == 1
    # one thermo (imported) + one species_entry (linked)
    assert body["summary"]["records_linked"] == 1

    # database side-effects
    assert after["thermo"] == before["thermo"] + 1
    assert after["submission"] == before["submission"] + 1
    # at least submission_created + ingestion_succeeded
    assert after["audit"] >= before["audit"] + 2
    # one product link + one species_entry link
    assert after["links"] == before["links"] + 2

    # actor wiring — testuser comes from conftest's _api_test_user
    test_user = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    submission = db_session.get(Submission, body["submission_id"])
    assert submission.created_by == test_user.id
    assert submission.status is SubmissionStatus.pending
    assert submission.is_public is False  # pending != approved

    thermo_id = next(
        r["record_id"] for r in body["records"] if r["record_type"] == "thermo"
    )
    thermo = db_session.get(Thermo, thermo_id)
    assert thermo.created_by == test_user.id

    # audit events: submission_created and ingestion_succeeded
    event_kinds = [
        e.event_kind
        for e in db_session.scalars(
            select(SubmissionAuditEvent).where(
                SubmissionAuditEvent.submission_id == submission.id
            )
        ).all()
    ]
    assert SubmissionAuditEventKind.submission_created in event_kinds
    assert SubmissionAuditEventKind.ingestion_succeeded in event_kinds

    # link record types — exactly thermo + species_entry, both linked to
    # this submission
    link_types = {
        link.record_type
        for link in db_session.scalars(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission.id
            )
        ).all()
    }
    assert link_types == {
        SubmissionRecordType.thermo,
        SubmissionRecordType.species_entry,
    }


def test_valid_kinetics_bundle_imports(client, db_session) -> None:
    bundle = _load_bundle("kinetics-bundle-v0.json")

    before = _counts(db_session)
    resp = client.post(ENDPOINT, json=bundle)
    after = _counts(db_session)

    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["bundle_kind"] == "kinetics"
    assert body["status"] == SubmissionStatus.pending.value
    assert body["review_status"] == "unreviewed"
    assert body["summary"]["records_imported"] == 1

    assert after["kinetics"] == before["kinetics"] + 1
    assert after["submission"] == before["submission"] + 1

    test_user = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    submission = db_session.get(Submission, body["submission_id"])
    assert submission.created_by == test_user.id

    kinetics_id = next(
        r["record_id"]
        for r in body["records"]
        if r["record_type"] == "kinetics"
    )
    kinetics = db_session.get(Kinetics, kinetics_id)
    assert kinetics.created_by == test_user.id

    link_types = {
        link.record_type
        for link in db_session.scalars(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission.id
            )
        ).all()
    }
    assert link_types == {
        SubmissionRecordType.kinetics,
        SubmissionRecordType.reaction_entry,
    }


# ---------------------------------------------------------------------------
# Gate / error / rollback tests
# ---------------------------------------------------------------------------


def test_dry_run_blocking_error_prevents_import(client, db_session) -> None:
    """A bundle that dry-run rejects must not write anything.

    Construct a kinetics bundle whose participant species cannot be
    canonicalized — the dry-run service emits an item with action=error
    when ``canonical_species_identity`` raises, which trips the strict
    gate.
    """
    bundle = _load_bundle("kinetics-bundle-v0.json")
    # Replace every reactant SMILES with an unparseable string so
    # canonicalization fails at the first participant.
    for participant in bundle["records"]["kinetics_uploads"][0]["reaction"][
        "reactants"
    ]:
        participant["species_entry"]["smiles"] = "this-is-not-a-smiles"

    before = _counts(db_session)
    resp = client.post(ENDPOINT, json=bundle)
    after = _counts(db_session)

    assert resp.status_code == 400, resp.text
    assert before == after  # no rows of any tracked kind


def test_invalid_bundle_returns_validation_error(client, db_session) -> None:
    """Structurally invalid bundles fail through normal Pydantic validation."""
    bundle = _load_bundle("thermo-bundle-v0.json")
    del bundle["bundle_kind"]

    before = _counts(db_session)
    resp = client.post(ENDPOINT, json=bundle)
    after = _counts(db_session)

    assert resp.status_code == 422
    assert before == after


def test_imported_records_are_unreviewed(client, db_session) -> None:
    """Response and submission row both surface unreviewed/pending state."""
    bundle = _load_bundle("thermo-bundle-v0.json")
    resp = client.post(ENDPOINT, json=bundle)
    assert resp.status_code == 201
    body = resp.json()

    assert body["status"] == "pending"
    assert body["review_status"] == "unreviewed"
    for rec in body["records"]:
        assert rec["review_status"] == "unreviewed"

    submission = db_session.get(Submission, body["submission_id"])
    assert submission.status is SubmissionStatus.pending
    assert submission.is_public is False


def test_local_exporter_label_does_not_become_hosted_actor(
    client, db_session
) -> None:
    """``bundle.exporter.local_user_label`` must never be the hosted actor.

    The example bundle's exporter label is ``example-user``. The hosted
    actor must remain the authenticated test user (``testuser``), and no
    AppUser row named ``example-user`` may be created during import.
    """
    bundle = _load_bundle("thermo-bundle-v0.json")
    assert bundle["exporter"]["local_user_label"] == "example-user"

    resp = client.post(ENDPOINT, json=bundle)
    assert resp.status_code == 201
    body = resp.json()

    test_user = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    submission = db_session.get(Submission, body["submission_id"])
    assert submission.created_by == test_user.id

    # No hosted user materialised from the local exporter label.
    smuggled = db_session.scalar(
        select(AppUser).where(AppUser.username == "example-user")
    )
    assert smuggled is None
