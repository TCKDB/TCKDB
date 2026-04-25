"""API tests for transition-state selection writes.

Exercises ``POST /api/v1/transition-states/{id}/selections`` including
parent-child consistency: an entry belonging to a different TS concept
must be rejected.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.app import create_app
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.db.models.transition_state import (
    TransitionState,
    TransitionStateEntry,
    TransitionStateSelection,
)


@pytest.fixture(autouse=True)
def _default_auth_as_curator(client, db_session) -> AppUser:
    """Default the TestClient's auth user to ``curator`` for this module.

    TS selection creation is now curator/admin-only, so the session-scoped
    role=``user`` test user would 403 on every POST. This autouse fixture
    keeps the legacy selection-create tests working without per-test churn.
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


_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"


def _ts_upload_payload() -> dict:
    return {
        "reaction": {
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            ],
            "products": [
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            ],
        },
        "charge": 0,
        "multiplicity": 1,
        "geometry": {"xyz_text": _XYZ_H2},
        "primary_opt": {
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "opt_result": {
                "converged": True,
                "n_steps": 10,
                "final_energy_hartree": -1.17,
            },
        },
        "additional_calculations": [
            {
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "freq_result": {
                    "n_imag": 1,
                    "imag_freq_cm1": -1500.0,
                    "zpe_hartree": 0.01,
                },
            },
        ],
    }


def _upload_ts(client) -> dict:
    return client.post(
        "/api/v1/uploads/transition-states", json=_ts_upload_payload()
    ).json()


class TestCreateTransitionStateSelection:
    def test_happy_path(self, client):
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]
        entry_id = upload["id"]

        resp = client.post(
            f"/api/v1/transition-states/{ts_id}/selections",
            json={
                "transition_state_entry_id": entry_id,
                "selection_kind": "validated_reference",
            },
        )
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["transition_state_id"] == ts_id
        assert body["transition_state_entry_id"] == entry_id
        assert body["selection_kind"] == "validated_reference"

    def test_parent_ts_missing(self, client):
        upload = _upload_ts(client)
        resp = client.post(
            "/api/v1/transition-states/999999/selections",
            json={
                "transition_state_entry_id": upload["id"],
                "selection_kind": "validated_reference",
            },
        )
        assert resp.status_code == 404
        assert "TransitionState" in resp.json()["detail"]

    def test_entry_missing(self, client):
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]
        resp = client.post(
            f"/api/v1/transition-states/{ts_id}/selections",
            json={
                "transition_state_entry_id": 999999,
                "selection_kind": "validated_reference",
            },
        )
        assert resp.status_code == 404
        assert "TransitionStateEntry" in resp.json()["detail"]

    def test_cross_ts_entry_rejected(self, client, db_session):
        # One TS is created via the normal upload path; the second TS +
        # entry are inserted directly so the test doesn't depend on the
        # upload workflow accepting a second payload shape.
        upload_a = _upload_ts(client)
        ts_a_id = upload_a["transition_state_id"]

        ts_b = TransitionState(
            reaction_entry_id=upload_a["reaction_entry_id"],
            label="ts-b",
        )
        db_session.add(ts_b)
        db_session.flush()
        entry_b = TransitionStateEntry(
            transition_state_id=ts_b.id,
            charge=0,
            multiplicity=1,
        )
        db_session.add(entry_b)
        db_session.flush()
        assert ts_a_id != ts_b.id

        resp = client.post(
            f"/api/v1/transition-states/{ts_a_id}/selections",
            json={
                "transition_state_entry_id": entry_b.id,
                "selection_kind": "validated_reference",
            },
        )
        assert resp.status_code == 400, resp.json()
        assert "does not belong" in resp.json()["detail"]

    def test_duplicate_kind_rejected(self, client):
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]
        entry_id = upload["id"]

        body = {
            "transition_state_entry_id": entry_id,
            "selection_kind": "display_default",
        }
        resp1 = client.post(
            f"/api/v1/transition-states/{ts_id}/selections", json=body
        )
        assert resp1.status_code == 201
        resp2 = client.post(
            f"/api/v1/transition-states/{ts_id}/selections", json=body
        )
        assert resp2.status_code == 400
        assert "already exists" in resp2.json()["detail"]

    def test_different_kinds_coexist(self, client):
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]
        entry_id = upload["id"]
        for kind in ("display_default", "validated_reference", "curator_pick"):
            resp = client.post(
                f"/api/v1/transition-states/{ts_id}/selections",
                json={
                    "transition_state_entry_id": entry_id,
                    "selection_kind": kind,
                },
            )
            assert resp.status_code == 201, (kind, resp.json())

    def test_selection_visible_in_ts_read(self, client):
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]
        entry_id = upload["id"]
        client.post(
            f"/api/v1/transition-states/{ts_id}/selections",
            json={
                "transition_state_entry_id": entry_id,
                "selection_kind": "display_default",
                "note": "from test",
            },
        )

        resp = client.get(f"/api/v1/transition-states/{ts_id}")
        assert resp.status_code == 200
        selections = resp.json()["selections"]
        assert len(selections) == 1
        assert selections[0]["selection_kind"] == "display_default"
        assert selections[0]["transition_state_entry_id"] == entry_id
        assert selections[0]["note"] == "from test"

    def test_invalid_selection_kind_rejected(self, client):
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]
        resp = client.post(
            f"/api/v1/transition-states/{ts_id}/selections",
            json={
                "transition_state_entry_id": upload["id"],
                "selection_kind": "not_a_real_kind",
            },
        )
        assert resp.status_code == 422


class TestCreateTransitionStateSelectionRoleGate:
    """Authorization: only curator/admin roles may create TS selections."""

    def test_curator_can_create(self, client):
        # Autouse fixture already sets the caller to curator.
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]
        resp = client.post(
            f"/api/v1/transition-states/{ts_id}/selections",
            json={
                "transition_state_entry_id": upload["id"],
                "selection_kind": "validated_reference",
            },
        )
        assert resp.status_code == 201, resp.json()

    def test_admin_can_create(self, client, as_admin):
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]
        resp = client.post(
            f"/api/v1/transition-states/{ts_id}/selections",
            json={
                "transition_state_entry_id": upload["id"],
                "selection_kind": "validated_reference",
            },
        )
        assert resp.status_code == 201, resp.json()

    def test_regular_user_gets_403(self, client, as_regular_user):
        # Uploads are not role-gated, so the upload still succeeds even
        # though the caller is already demoted to the ``user`` role by the
        # explicit fixture (which runs after the module's autouse curator).
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]

        resp = client.post(
            f"/api/v1/transition-states/{ts_id}/selections",
            json={
                "transition_state_entry_id": upload["id"],
                "selection_kind": "validated_reference",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Curator or admin role required."

    def test_403_does_not_persist_row(
        self, client, db_session, as_regular_user
    ):
        upload = _upload_ts(client)
        ts_id = upload["transition_state_id"]
        resp = client.post(
            f"/api/v1/transition-states/{ts_id}/selections",
            json={
                "transition_state_entry_id": upload["id"],
                "selection_kind": "validated_reference",
            },
        )
        assert resp.status_code == 403
        rows = db_session.scalars(
            select(TransitionStateSelection).where(
                TransitionStateSelection.transition_state_id == ts_id
            )
        ).all()
        assert rows == []

    def test_missing_api_key_returns_401(self, db_engine, _api_test_user):
        """A TestClient without the auth override must be rejected with 401."""
        app = create_app()
        with TestClient(app) as c:
            resp = c.post(
                "/api/v1/transition-states/1/selections",
                json={
                    "transition_state_entry_id": 1,
                    "selection_kind": "validated_reference",
                },
            )
            assert resp.status_code == 401
