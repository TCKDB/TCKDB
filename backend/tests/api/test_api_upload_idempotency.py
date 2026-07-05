"""API tests for Idempotency-Key on upload endpoints.

Covers happy-path replay, payload conflict, scope by user/endpoint,
no-key passthrough, invalid keys, validation-failure non-storage,
rolled-back-write non-storage, and expired-key-as-new behaviour.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.app import create_app
from app.api.deps import get_current_user, get_db, get_write_db
from app.db.models.api_key import ApiKey
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.db.models.idempotency import IdempotencyRecord
from app.db.models.species import ConformerObservation

CONFORMER_ENDPOINT = "/api/v1/uploads/conformers"
THERMO_ENDPOINT = "/api/v1/uploads/thermo"
NETWORK_ENDPOINT = "/api/v1/uploads/networks"
KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotency-Replayed"


def _hydrogen_conformer_payload(label: str = "conf-a") -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": label,
        "note": "test upload",
    }


def _idempotency_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(IdempotencyRecord)) or 0


def _conformer_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(ConformerObservation)) or 0


# ---------------------------------------------------------------------------
# 1. First keyed upload stores response
# ---------------------------------------------------------------------------


class TestFirstKeyedUpload:
    def test_first_keyed_upload_stores_record(self, client, db_session) -> None:
        before_idem = _idempotency_count(db_session)
        before_conf = _conformer_count(db_session)
        resp = client.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(),
            headers={KEY_HEADER: "first-key-aaaaaaaaaaaaa"},
        )
        assert resp.status_code == 201, resp.text
        assert REPLAYED_HEADER not in resp.headers
        assert _idempotency_count(db_session) == before_idem + 1
        assert _conformer_count(db_session) == before_conf + 1

        rec = db_session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "first-key-aaaaaaaaaaaaa"
            )
        )
        assert rec is not None
        assert rec.status_code == 201
        assert rec.response_body_json["type"] == "conformer_observation"
        assert rec.expires_at > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=29)


# ---------------------------------------------------------------------------
# 2. Replay returns stored response and skips the write
# ---------------------------------------------------------------------------


class TestReplay:
    def test_replay_returns_stored_response(self, client, db_session) -> None:
        payload = _hydrogen_conformer_payload()
        first = client.post(
            CONFORMER_ENDPOINT,
            json=payload,
            headers={KEY_HEADER: "replay-key-aaaaaaaaaaaaa"},
        )
        assert first.status_code == 201
        before_conf = _conformer_count(db_session)
        before_idem = _idempotency_count(db_session)

        second = client.post(
            CONFORMER_ENDPOINT,
            json=payload,
            headers={KEY_HEADER: "replay-key-aaaaaaaaaaaaa"},
        )
        assert second.status_code == 201
        assert second.headers.get(REPLAYED_HEADER) == "true"
        assert second.json() == first.json()
        # No new conformer or idempotency rows.
        assert _conformer_count(db_session) == before_conf
        assert _idempotency_count(db_session) == before_idem

    def test_replay_replays_status_code(self, client, db_session) -> None:
        # POST /uploads/conformers returns 201; replay must too.
        client.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(),
            headers={KEY_HEADER: "replay-status-aaaaaaaaa"},
        )
        replay = client.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(),
            headers={KEY_HEADER: "replay-status-aaaaaaaaa"},
        )
        assert replay.status_code == 201

    def test_reordered_payload_keys_still_replay(self, client, db_session) -> None:
        payload_a = _hydrogen_conformer_payload()
        payload_b = {
            "note": payload_a["note"],
            "label": payload_a["label"],
            "calculation": payload_a["calculation"],
            "geometry": payload_a["geometry"],
            "species_entry": payload_a["species_entry"],
        }
        first = client.post(
            CONFORMER_ENDPOINT,
            json=payload_a,
            headers={KEY_HEADER: "reorder-key-aaaaaaaaaaaa"},
        )
        assert first.status_code == 201
        second = client.post(
            CONFORMER_ENDPOINT,
            json=payload_b,
            headers={KEY_HEADER: "reorder-key-aaaaaaaaaaaa"},
        )
        assert second.status_code == 201
        assert second.headers.get(REPLAYED_HEADER) == "true"


# ---------------------------------------------------------------------------
# 3. Same key + different payload → 409 idempotency_conflict
# ---------------------------------------------------------------------------


class TestConflict:
    def test_same_key_different_payload_returns_409(
        self, client, db_session
    ) -> None:
        client.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(label="conf-a"),
            headers={KEY_HEADER: "conflict-key-aaaaaaaaaaa"},
        )
        before = _conformer_count(db_session)
        conflict = client.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(label="conf-b"),
            headers={KEY_HEADER: "conflict-key-aaaaaaaaaaa"},
        )
        assert conflict.status_code == 409
        body = conflict.json()
        assert body["code"] == "idempotency_conflict"
        assert body["endpoint"] == CONFORMER_ENDPOINT
        # Conflict request must NOT have written anything.
        assert _conformer_count(db_session) == before


# ---------------------------------------------------------------------------
# 4. Scoping: user / endpoint / method
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def second_user_id(db_engine) -> int:
    """Create a second test user (committed) for cross-user scoping tests."""
    second_key_value = "second-test-api-key"
    second_hash = hashlib.sha256(second_key_value.encode()).hexdigest()
    from sqlalchemy.orm import Session

    with Session(db_engine) as session:
        with session.begin():
            existing = session.scalar(
                select(AppUser).where(AppUser.username == "seconduser")
            )
            if existing is not None:
                return existing.id
            user = AppUser(username="seconduser", role=AppUserRole.user)
            session.add(user)
            session.flush()
            session.add(
                ApiKey(
                    user_id=user.id,
                    key_hash=second_hash,
                    label="second pytest key",
                )
            )
            session.flush()
            return user.id


class TestScoping:
    def test_same_key_different_users_no_conflict(
        self, db_engine, _api_test_user, second_user_id, db_session
    ) -> None:
        """Both users use the same key on the same endpoint — both succeed."""

        # Build two TestClients sharing the txn-scoped session, each
        # forced to a different current_user.
        def make_client(user_id: int) -> TestClient:
            app = create_app()
            app.dependency_overrides[get_db] = lambda: db_session
            app.dependency_overrides[get_write_db] = lambda: db_session
            user = db_session.get(AppUser, user_id)
            app.dependency_overrides[get_current_user] = lambda: user
            return TestClient(app)

        c1 = make_client(_api_test_user)
        c2 = make_client(second_user_id)
        shared_key = "shared-key-aaaaaaaaaaaa"

        r1 = c1.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(label="user1-conf"),
            headers={KEY_HEADER: shared_key},
        )
        r2 = c2.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(label="user2-conf"),
            headers={KEY_HEADER: shared_key},
        )
        assert r1.status_code == 201, r1.text
        assert r2.status_code == 201, r2.text
        assert REPLAYED_HEADER not in r1.headers
        assert REPLAYED_HEADER not in r2.headers

    def test_same_key_different_endpoints_no_conflict(
        self, client, db_session
    ) -> None:
        """One user, one key, two endpoints — no conflict."""
        shared_key = "endpoint-scope-aaaaaaaaa"
        r1 = client.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(label="conf-x"),
            headers={KEY_HEADER: shared_key},
        )
        assert r1.status_code == 201, r1.text
        # Same key reused on /uploads/networks should not collide; the
        # network call will likely 422 on its own validation, but the
        # idempotency layer must not pre-empt that with a conflict.
        r2 = client.post(
            NETWORK_ENDPOINT,
            json={"unrelated": "payload"},
            headers={KEY_HEADER: shared_key},
        )
        assert r2.status_code != 409 or r2.json().get("code") != "idempotency_conflict"


# ---------------------------------------------------------------------------
# 5. Passthrough behaviour: no key, invalid key, header on read endpoints
# ---------------------------------------------------------------------------


class TestPassthrough:
    def test_no_key_still_works(self, client, db_session) -> None:
        before_idem = _idempotency_count(db_session)
        resp = client.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(label="no-key-conf"),
        )
        assert resp.status_code == 201, resp.text
        assert REPLAYED_HEADER not in resp.headers
        # No idempotency record created when no key sent.
        assert _idempotency_count(db_session) == before_idem

    @pytest.mark.parametrize("bad_key", ["short", "x" * 15, "x" * 201, "has space00000000"])
    def test_invalid_key_returns_400(self, client, bad_key) -> None:
        resp = client.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(),
            headers={KEY_HEADER: bad_key},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "invalid_idempotency_key"


# ---------------------------------------------------------------------------
# 6. Failure paths must not store
# ---------------------------------------------------------------------------


class TestFailureNotStored:
    def test_validation_failure_leaves_no_record(
        self, client, db_session
    ) -> None:
        before = _idempotency_count(db_session)
        bad_payload = _hydrogen_conformer_payload()
        bad_payload["species_entry"]["smiles"] = "NOT_A_SMILES"
        resp = client.post(
            CONFORMER_ENDPOINT,
            json=bad_payload,
            headers={KEY_HEADER: "valfail-key-aaaaaaaaaaa"},
        )
        assert resp.status_code == 422
        assert _idempotency_count(db_session) == before

        # The same key may now be reused with a valid payload.
        good = client.post(
            CONFORMER_ENDPOINT,
            json=_hydrogen_conformer_payload(label="recovered-conf"),
            headers={KEY_HEADER: "valfail-key-aaaaaaaaaaa"},
        )
        assert good.status_code == 201, good.text

    def test_workflow_failure_leaves_no_record(
        self, client, db_session, monkeypatch
    ) -> None:
        """When the workflow raises, the route never reaches ``idem.record``.

        Combined with ``get_write_db``'s rollback-on-exception contract in
        production, this guarantees no idempotency record is left behind
        for a failed write. The test exercises the structural property:
        the workflow raises, the route returns no response, no record is
        added to the session.
        """
        from app.api.routes import uploads as uploads_module

        def boom(*args, **kwargs):
            raise RuntimeError("simulated workflow failure")

        monkeypatch.setattr(uploads_module, "persist_conformer_upload", boom)

        before = _idempotency_count(db_session)
        with pytest.raises(RuntimeError):
            client.post(
                CONFORMER_ENDPOINT,
                json=_hydrogen_conformer_payload(label="rollback-conf"),
                headers={KEY_HEADER: "rollback-key-aaaaaaaaaa"},
            )
        assert _idempotency_count(db_session) == before


# ---------------------------------------------------------------------------
# 7. Expired key behaves as a new request
# ---------------------------------------------------------------------------


class TestExpiredKey:
    def test_expired_record_treated_as_new(self, client, db_session) -> None:
        # Plant an expired record directly so the next upload skips replay.
        from app.services.idempotency import canonical_payload_hash

        payload = _hydrogen_conformer_payload(label="expired-conf")
        payload_hash = canonical_payload_hash(payload)
        user = db_session.scalar(select(AppUser).where(AppUser.username == "testuser"))

        stale = IdempotencyRecord(
            user_id=user.id,
            request_method="POST",
            endpoint=CONFORMER_ENDPOINT,
            idempotency_key="expired-key-aaaaaaaaaaaa",
            payload_hash=payload_hash,
            status_code=201,
            response_body_json={"id": -1, "type": "stale"},
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
        )
        db_session.add(stale)
        db_session.flush()

        resp = client.post(
            CONFORMER_ENDPOINT,
            json=payload,
            headers={KEY_HEADER: "expired-key-aaaaaaaaaaaa"},
        )
        assert resp.status_code == 201, resp.text
        # New request was processed, not replayed.
        assert REPLAYED_HEADER not in resp.headers
        assert resp.json().get("id", -1) != -1
