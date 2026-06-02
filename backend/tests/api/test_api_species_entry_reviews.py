"""API tests for species-entry review create/list endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    MoleculeKind,
    SpeciesEntryReviewRole,
    StereoKind,
)
from app.db.models.species import Species, SpeciesEntry, SpeciesEntryReview


@pytest.fixture
def as_curator(client, db_session) -> AppUser:
    """Default the TestClient's auth user to the ``curator`` role.

    The session-scoped fixture user is role=``user``; review creation now
    requires curator/admin, so tests that post reviews must opt into a
    curation role. Tests that specifically verify the 403 path flip the role
    back to ``user`` via :func:`as_regular_user`.
    """
    user = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    user.role = AppUserRole.curator
    db_session.flush()
    return user


@pytest.fixture
def as_regular_user(client, db_session) -> AppUser:
    """Force the TestClient's auth user to the plain ``user`` role for 403 checks."""
    user = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    user.role = AppUserRole.user
    db_session.flush()
    return user


@pytest.fixture
def as_admin(client, db_session) -> AppUser:
    """Force the TestClient's auth user to the ``admin`` role."""
    user = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    user.role = AppUserRole.admin
    db_session.flush()
    return user


def _make_species_entry(session, smiles: str = "[H]") -> int:
    """Seed a species + entry directly on the DB session and return entry id.

    The API tests bypass the upload workflow here because species-entry
    reviews are orthogonal to ingestion — they only need an existing entry.
    """
    inchi_key_by_smiles = {
        "[H]": "YZCKVEUIGOORGS-UHFFFAOYSA-N",
        "[He]": "SWQJXJOGLNCZEY-UHFFFAOYSA-N",
    }
    species = Species(
        kind=MoleculeKind.molecule,
        smiles=smiles,
        inchi_key=inchi_key_by_smiles[smiles],
        charge=0,
        multiplicity=2 if smiles == "[H]" else 1,
        stereo_kind=StereoKind.achiral,
    )
    session.add(species)
    session.flush()
    entry = SpeciesEntry(species_id=species.id)
    session.add(entry)
    session.flush()
    return entry.id


class TestCreateSpeciesEntryReview:
    def test_happy_path(self, client, db_session, as_curator):
        entry_id = _make_species_entry(db_session)
        resp = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator", "note": "looks good"},
        )
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["species_entry_id"] == entry_id
        assert body["role"] == "curator"
        assert body["note"] == "looks good"
        assert body["id"] is not None
        assert "user_id" in body
        assert "created_at" in body

    def test_reviewer_id_comes_from_auth_not_body(
        self, client, db_session, as_curator
    ):
        """user_id in the request body must be ignored (extra-fields rejected)."""
        entry_id = _make_species_entry(db_session)

        # Explicit user_id in the payload is rejected by schema.
        resp = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator", "user_id": 999_999},
        )
        assert resp.status_code == 422

        # Without the forbidden field, the review binds to the auth user.
        resp = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator"},
        )
        assert resp.status_code == 201
        assert resp.json()["user_id"] == as_curator.id

    def test_missing_species_entry_returns_404(self, client, as_curator):
        resp = client.post(
            "/api/v1/species-entries/999999/reviews",
            json={"role": "curator"},
        )
        assert resp.status_code == 404
        assert "SpeciesEntry" in resp.json()["detail"]

    def test_invalid_role_rejected(self, client, db_session, as_curator):
        entry_id = _make_species_entry(db_session)
        resp = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "approved"},
        )
        assert resp.status_code == 422

    def test_duplicate_same_role_by_same_user_rejected(
        self, client, db_session, as_curator
    ):
        entry_id = _make_species_entry(db_session)
        r1 = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator"},
        )
        assert r1.status_code == 201
        r2 = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator"},
        )
        assert r2.status_code == 400
        assert "already exists" in r2.json()["detail"]

    def test_different_roles_append(self, client, db_session, as_curator):
        entry_id = _make_species_entry(db_session)
        for role in ("curator", "reviewer", "validator"):
            resp = client.post(
                f"/api/v1/species-entries/{entry_id}/reviews",
                json={"role": role},
            )
            assert resp.status_code == 201, (role, resp.json())


class TestCreateSpeciesEntryReviewRoleGate:
    """Authorization: only curator/admin roles may create reviews."""

    def test_curator_can_create(self, client, db_session, as_curator):
        entry_id = _make_species_entry(db_session)
        resp = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator"},
        )
        assert resp.status_code == 201, resp.json()

    def test_admin_can_create(self, client, db_session, as_admin):
        entry_id = _make_species_entry(db_session)
        resp = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator"},
        )
        assert resp.status_code == 201, resp.json()

    def test_regular_user_gets_403(
        self, client, db_session, as_regular_user
    ):
        entry_id = _make_species_entry(db_session)
        resp = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Curator or admin role required."

    def test_403_does_not_persist_row(
        self, client, db_session, as_regular_user
    ):
        entry_id = _make_species_entry(db_session)
        resp = client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator"},
        )
        assert resp.status_code == 403
        rows = db_session.scalars(
            select(SpeciesEntryReview).where(
                SpeciesEntryReview.species_entry_id == entry_id
            )
        ).all()
        assert rows == []


class TestListSpeciesEntryReviews:
    def test_empty_list(self, client, db_session):
        entry_id = _make_species_entry(db_session)
        resp = client.get(f"/api/v1/species-entries/{entry_id}/reviews")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_missing_species_entry_returns_404(self, client):
        resp = client.get("/api/v1/species-entries/999999/reviews")
        assert resp.status_code == 404

    def test_newest_first_ordering(self, client, db_session, as_curator):
        entry_id = _make_species_entry(db_session)
        client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator"},
        )
        client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "reviewer"},
        )

        resp = client.get(f"/api/v1/species-entries/{entry_id}/reviews")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        # Secondary id.desc() guarantees deterministic order even when the
        # two created_at values collide at second granularity.
        assert rows[0]["id"] > rows[1]["id"]
        assert rows[0]["role"] == "reviewer"
        assert rows[1]["role"] == "curator"

    def test_list_is_scoped_to_requested_entry(
        self, client, db_session, as_curator
    ):
        entry_a = _make_species_entry(db_session, smiles="[H]")
        entry_b = _make_species_entry(db_session, smiles="[He]")

        client.post(
            f"/api/v1/species-entries/{entry_a}/reviews",
            json={"role": "curator", "note": "A"},
        )
        client.post(
            f"/api/v1/species-entries/{entry_b}/reviews",
            json={"role": "curator", "note": "B"},
        )

        a_rows = client.get(
            f"/api/v1/species-entries/{entry_a}/reviews"
        ).json()
        b_rows = client.get(
            f"/api/v1/species-entries/{entry_b}/reviews"
        ).json()
        assert len(a_rows) == 1 and a_rows[0]["note"] == "A"
        assert len(b_rows) == 1 and b_rows[0]["note"] == "B"

    def test_list_unaffected_by_user_role(
        self, client, db_session, as_curator
    ):
        """List reads are not gated by the new curator/admin create rule."""
        entry_id = _make_species_entry(db_session)
        client.post(
            f"/api/v1/species-entries/{entry_id}/reviews",
            json={"role": "curator"},
        )
        # Downgrade the caller to plain user and re-read — list must still work.
        as_curator.role = AppUserRole.user
        db_session.flush()

        resp = client.get(f"/api/v1/species-entries/{entry_id}/reviews")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestCreateSpeciesEntryReviewAuth:
    def test_missing_api_key_returns_401(self, db_engine, _api_test_user):
        """A TestClient without the auth override must be rejected with 401."""
        app = create_app()
        # No override on get_current_user: the real API-key header check runs.
        with TestClient(app) as c:
            # The entry id is irrelevant — auth runs before resource lookup.
            resp = c.post(
                "/api/v1/species-entries/1/reviews",
                json={"role": "curator"},
            )
            assert resp.status_code == 401

    def test_list_does_not_require_api_key(self, db_engine, _api_test_user):
        """Reads follow the existing public-read convention on species-entry routes.

        Builds a raw ``create_app()`` so the real auth chain runs (no
        ``get_current_user`` override), but binds ``get_db`` to the migrated
        test engine. Without that binding the app falls back to
        ``settings.db_name`` (``tckdb_dev``), a developer-owned DB outside test
        control whose schema may lag the ORM — which makes this a flaky read
        against an arbitrary database rather than a real auth assertion.
        """
        app = create_app()
        connection = db_engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        app.dependency_overrides[get_db] = lambda: session
        try:
            with TestClient(app) as c:
                # Expect 404 (missing entry), not 401 — proves auth isn't gating reads.
                resp = c.get("/api/v1/species-entries/999999/reviews")
                assert resp.status_code == 404
        finally:
            session.close()
            transaction.rollback()
            connection.close()


def test_review_row_persists_in_db(client, db_session, as_curator):
    """End-to-end: POST creates a row visible via direct ORM query."""
    entry_id = _make_species_entry(db_session)
    resp = client.post(
        f"/api/v1/species-entries/{entry_id}/reviews",
        json={"role": "curator", "note": "checked"},
    )
    assert resp.status_code == 201
    review_id = resp.json()["id"]

    row = db_session.get(SpeciesEntryReview, review_id)
    assert row is not None
    assert row.species_entry_id == entry_id
    assert row.role is SpeciesEntryReviewRole.curator
    assert row.note == "checked"
    # Reviewer is the authenticated test user, not client-supplied.
    assert row.user_id == as_curator.id
