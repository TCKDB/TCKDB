"""API tests for conformer-group selection writes.

These exercise ``POST /api/v1/conformer-groups/{id}/selections``. The
schema's ``ConformerSelection`` is scoped to ``(group, assignment_scheme,
selection_kind)`` — it does not target a specific observation — so
parent-child validation here is limited to checking that the parent group
exists.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.app import create_app
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.db.models.species import ConformerSelection


@pytest.fixture(autouse=True)
def _default_auth_as_curator(client, db_session) -> AppUser:
    """Default the TestClient's auth user to ``curator`` for this module.

    Selection creation is now curator/admin-only, so the session-scoped
    role=``user`` test user would 403 on every POST. This autouse fixture
    keeps the legacy selection-create tests working without per-test churn.
    Tests that specifically verify the 403 path request :func:`as_regular_user`
    to demote the role back down after this fixture runs.
    """
    user = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    user.role = AppUserRole.curator
    db_session.flush()
    return user


@pytest.fixture
def as_admin(db_session) -> AppUser:
    """Force the TestClient's auth user to the ``admin`` role."""
    user = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    user.role = AppUserRole.admin
    db_session.flush()
    return user


@pytest.fixture
def as_regular_user(db_session) -> AppUser:
    """Force the TestClient's auth user to the plain ``user`` role for 403 checks."""
    user = db_session.scalar(
        select(AppUser).where(AppUser.username == "testuser")
    )
    user.role = AppUserRole.user
    db_session.flush()
    return user


def _hydrogen_conformer_payload(label: str = "conf-a") -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": label,
    }


def _create_group(client) -> int:
    client.post(
        "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
    )
    return client.get("/api/v1/conformer-groups").json()["items"][0]["id"]


class TestCreateConformerSelection:
    def test_happy_path(self, client):
        group_id = _create_group(client)
        resp = client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "display_default"},
        )
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["conformer_group_id"] == group_id
        assert body["selection_kind"] == "display_default"
        assert body["assignment_scheme_id"] is None
        assert body["id"] is not None

    def test_parent_group_missing(self, client):
        resp = client.post(
            "/api/v1/conformer-groups/999999/selections",
            json={"selection_kind": "display_default"},
        )
        assert resp.status_code == 404
        assert "ConformerGroup" in resp.json()["detail"]

    def test_assignment_scheme_missing(self, client):
        group_id = _create_group(client)
        resp = client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={
                "selection_kind": "display_default",
                "assignment_scheme_id": 999999,
            },
        )
        assert resp.status_code == 404
        assert "ConformerAssignmentScheme" in resp.json()["detail"]

    def test_duplicate_kind_rejected(self, client):
        group_id = _create_group(client)
        resp1 = client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "display_default"},
        )
        assert resp1.status_code == 201
        resp2 = client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "display_default"},
        )
        assert resp2.status_code == 400
        assert "already exists" in resp2.json()["detail"]

    def test_different_kinds_coexist(self, client):
        group_id = _create_group(client)
        for kind in ("display_default", "lowest_energy", "curator_pick"):
            resp = client.post(
                f"/api/v1/conformer-groups/{group_id}/selections",
                json={"selection_kind": kind},
            )
            assert resp.status_code == 201, (kind, resp.json())

    def test_selection_visible_in_group_read(self, client):
        group_id = _create_group(client)
        client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "display_default", "note": "from test"},
        )

        resp = client.get(f"/api/v1/conformer-groups/{group_id}")
        assert resp.status_code == 200
        selections = resp.json()["selections"]
        assert len(selections) == 1
        assert selections[0]["selection_kind"] == "display_default"
        assert selections[0]["note"] == "from test"

    def test_selection_visible_via_nested_list_endpoint(self, client):
        group_id = _create_group(client)
        client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "curator_pick"},
        )
        resp = client.get(f"/api/v1/conformer-groups/{group_id}/selections")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["selection_kind"] == "curator_pick"

    def test_nested_list_endpoint_404_for_missing_group(self, client):
        resp = client.get("/api/v1/conformer-groups/999999/selections")
        assert resp.status_code == 404

    def test_invalid_selection_kind_rejected(self, client):
        group_id = _create_group(client)
        resp = client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "not_a_real_kind"},
        )
        assert resp.status_code == 422


class TestCreateConformerSelectionRoleGate:
    """Authorization: only curator/admin roles may create conformer selections."""

    def test_curator_can_create(self, client):
        # Autouse fixture already sets the caller to curator.
        group_id = _create_group(client)
        resp = client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "display_default"},
        )
        assert resp.status_code == 201, resp.json()

    def test_admin_can_create(self, client, as_admin):
        group_id = _create_group(client)
        resp = client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "display_default"},
        )
        assert resp.status_code == 201, resp.json()

    def test_regular_user_gets_403(self, client, as_regular_user):
        # Uploads are not role-gated, so the group is created successfully
        # even though the caller is already demoted to the ``user`` role by
        # the explicit fixture (which runs after the module's autouse curator).
        group_id = _create_group(client)

        resp = client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "display_default"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Curator or admin role required."

    def test_403_does_not_persist_row(self, client, db_session, as_regular_user):
        group_id = _create_group(client)
        resp = client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": "display_default"},
        )
        assert resp.status_code == 403
        rows = db_session.scalars(
            select(ConformerSelection).where(
                ConformerSelection.conformer_group_id == group_id
            )
        ).all()
        assert rows == []

    def test_missing_api_key_returns_401(self, db_engine, _api_test_user):
        """A TestClient without the auth override must be rejected with 401."""
        app = create_app()
        with TestClient(app) as c:
            # Group id is irrelevant — auth runs before resource lookup.
            resp = c.post(
                "/api/v1/conformer-groups/1/selections",
                json={"selection_kind": "display_default"},
            )
            assert resp.status_code == 401
