"""Service-level tests for the idempotency layer.

Covers canonical hashing, key validation, lookup/conflict detection,
recording, and TTL/cleanup helpers. Route-level integration tests live
in tests/api/test_api_upload_idempotency.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db.models.idempotency import IdempotencyRecord
from app.services.idempotency import (
    IDEMPOTENCY_TTL,
    IdempotencyConflict,
    InvalidIdempotencyKey,
    canonical_payload_hash,
    delete_expired_records,
    find_existing_record,
    lookup_or_conflict,
    record_response,
    validate_idempotency_key,
)

# ---------------------------------------------------------------------------
# Canonical payload hash
# ---------------------------------------------------------------------------


class TestCanonicalPayloadHash:
    def test_hash_is_deterministic_for_reordered_keys(self) -> None:
        a = {"x": 1, "y": 2, "z": [1, 2, 3]}
        b = {"z": [1, 2, 3], "y": 2, "x": 1}
        assert canonical_payload_hash(a) == canonical_payload_hash(b)

    def test_hash_is_deterministic_for_nested_reorder(self) -> None:
        a = {"outer": {"a": 1, "b": [{"k": 1, "v": 2}]}}
        b = {"outer": {"b": [{"v": 2, "k": 1}], "a": 1}}
        assert canonical_payload_hash(a) == canonical_payload_hash(b)

    def test_hash_changes_when_value_changes(self) -> None:
        a = {"x": 1}
        b = {"x": 2}
        assert canonical_payload_hash(a) != canonical_payload_hash(b)

    def test_hash_changes_when_field_added(self) -> None:
        a = {"x": 1}
        b = {"x": 1, "y": None}
        assert canonical_payload_hash(a) != canonical_payload_hash(b)

    def test_list_order_affects_hash(self) -> None:
        a = [1, 2, 3]
        b = [3, 2, 1]
        assert canonical_payload_hash(a) != canonical_payload_hash(b)

    def test_returns_64_char_hex(self) -> None:
        h = canonical_payload_hash({"hi": "there"})
        assert len(h) == 64
        int(h, 16)  # parses as hex


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------


class TestValidateIdempotencyKey:
    @pytest.mark.parametrize(
        "key",
        [
            "abcdefghijklmnop",  # 16 chars, lowercase
            "ABCDEFGHIJKLMNOP",  # 16 chars, uppercase
            "arc:job-123:conformer:ethanol",
            "notebook.2026-04-25_thermo:ethanol",
            "0123456789ABCDEFabcdef",
            "x" * 200,
        ],
    )
    def test_valid_keys_accepted(self, key: str) -> None:
        assert validate_idempotency_key(key) == key

    @pytest.mark.parametrize(
        "key",
        [
            "",
            "tooshort",  # < 16
            "x" * 15,  # < 16
            "x" * 201,  # > 200
            "has space",
            "has/slash00000000",
            "has?query0000000000000",
            "has\u00e9accent00000000",  # non-ASCII
            "trailing\n0000000000",
        ],
    )
    def test_invalid_keys_rejected(self, key: str) -> None:
        with pytest.raises(InvalidIdempotencyKey):
            validate_idempotency_key(key)


# ---------------------------------------------------------------------------
# Find / lookup / conflict
# ---------------------------------------------------------------------------


class TestLookupAndConflict:
    def _record(
        self,
        db_session,
        user_id: int,
        *,
        key: str = "key-aaaaaaaaaaaaaaa",
        method: str = "POST",
        endpoint: str = "/api/v1/uploads/conformers",
        payload_hash: str = "a" * 64,
        status_code: int = 201,
        body=None,
        expires_in: timedelta | None = None,
    ) -> IdempotencyRecord:
        rec = record_response(
            db_session,
            user_id=user_id,
            request_method=method,
            endpoint=endpoint,
            idempotency_key=key,
            payload_hash=payload_hash,
            status_code=status_code,
            response_body=body or {"id": 1},
        )
        if expires_in is not None:
            rec.expires_at = datetime.utcnow() + expires_in
        db_session.flush()
        return rec

    def test_find_returns_none_when_no_record(self, db_session, _api_test_user) -> None:
        result = find_existing_record(
            db_session,
            user_id=_api_test_user,
            request_method="POST",
            endpoint="/api/v1/uploads/conformers",
            idempotency_key="missing-key-xxxxxxxx",
        )
        assert result is None

    def test_find_returns_record_when_match(self, db_session, _api_test_user) -> None:
        rec = self._record(db_session, _api_test_user, key="key-bbbbbbbbbbbbbbb")
        found = find_existing_record(
            db_session,
            user_id=_api_test_user,
            request_method="POST",
            endpoint="/api/v1/uploads/conformers",
            idempotency_key="key-bbbbbbbbbbbbbbb",
        )
        assert found is not None
        assert found.id == rec.id

    def test_find_ignores_expired(self, db_session, _api_test_user) -> None:
        self._record(
            db_session,
            _api_test_user,
            key="key-cccccccccccccccc",
            expires_in=timedelta(seconds=-1),
        )
        found = find_existing_record(
            db_session,
            user_id=_api_test_user,
            request_method="POST",
            endpoint="/api/v1/uploads/conformers",
            idempotency_key="key-cccccccccccccccc",
        )
        assert found is None

    def test_lookup_or_conflict_replays_on_match(
        self, db_session, _api_test_user
    ) -> None:
        self._record(db_session, _api_test_user, key="key-dddddddddddddddd")
        found = lookup_or_conflict(
            db_session,
            user_id=_api_test_user,
            request_method="POST",
            endpoint="/api/v1/uploads/conformers",
            idempotency_key="key-dddddddddddddddd",
            payload_hash="a" * 64,
        )
        assert found is not None

    def test_lookup_or_conflict_raises_on_payload_mismatch(
        self, db_session, _api_test_user
    ) -> None:
        self._record(db_session, _api_test_user, key="key-eeeeeeeeeeeeeeee")
        with pytest.raises(IdempotencyConflict) as exc_info:
            lookup_or_conflict(
                db_session,
                user_id=_api_test_user,
                request_method="POST",
                endpoint="/api/v1/uploads/conformers",
                idempotency_key="key-eeeeeeeeeeeeeeee",
                payload_hash="b" * 64,
            )
        assert exc_info.value.endpoint == "/api/v1/uploads/conformers"
        assert exc_info.value.in_progress is False


# ---------------------------------------------------------------------------
# record_response: shape / TTL / no-flush contract
# ---------------------------------------------------------------------------


class TestRecordResponse:
    def test_record_sets_expires_at_30_days_out(
        self, db_session, _api_test_user
    ) -> None:
        before = datetime.utcnow()
        rec = record_response(
            db_session,
            user_id=_api_test_user,
            request_method="POST",
            endpoint="/api/v1/uploads/thermo",
            idempotency_key="key-ffffffffffffffff",
            payload_hash="c" * 64,
            status_code=201,
            response_body={"id": 1, "type": "thermo"},
        )
        db_session.flush()
        after = datetime.utcnow()
        delta = rec.expires_at - before
        assert IDEMPOTENCY_TTL - timedelta(seconds=2) <= delta <= IDEMPOTENCY_TTL + (
            after - before
        ) + timedelta(seconds=2)

    def test_record_persists_response_body_as_jsonb(
        self, db_session, _api_test_user
    ) -> None:
        body = {"id": 42, "type": "thermo", "warnings": [{"field": "x"}]}
        rec = record_response(
            db_session,
            user_id=_api_test_user,
            request_method="POST",
            endpoint="/api/v1/uploads/thermo",
            idempotency_key="key-gggggggggggggggg",
            payload_hash="d" * 64,
            status_code=201,
            response_body=body,
        )
        db_session.flush()
        db_session.expire(rec)
        assert rec.response_body_json == body
        assert rec.status_code == 201


# ---------------------------------------------------------------------------
# Cleanup helper
# ---------------------------------------------------------------------------


class TestDeleteExpiredRecords:
    def test_deletes_only_expired(self, db_session, _api_test_user) -> None:
        live = record_response(
            db_session,
            user_id=_api_test_user,
            request_method="POST",
            endpoint="/api/v1/uploads/thermo",
            idempotency_key="live-aaaaaaaaaaaaaa",
            payload_hash="e" * 64,
            status_code=201,
            response_body={"id": 1},
        )
        dead = record_response(
            db_session,
            user_id=_api_test_user,
            request_method="POST",
            endpoint="/api/v1/uploads/thermo",
            idempotency_key="dead-aaaaaaaaaaaaaa",
            payload_hash="f" * 64,
            status_code=201,
            response_body={"id": 2},
        )
        dead.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db_session.flush()

        deleted = delete_expired_records(db_session)
        db_session.flush()
        assert deleted == 1
        assert db_session.get(IdempotencyRecord, live.id) is not None
        assert db_session.get(IdempotencyRecord, dead.id) is None
