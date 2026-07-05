"""Idempotency-key service for retry-safe write endpoints.

See ``docs/specs/upload-idempotency-key-spec.md`` for the contract and
``docs/decisions/0024-upload-idempotency-keys.md`` for the design choices.

This service stores the canonical SHA-256 hash of the request body and
the successful response for the tuple
``(user_id, request_method, endpoint, idempotency_key)`` for 30 days.
Repeated requests with the same key and same payload replay the stored
response instead of executing the write again. Repeated requests with
the same key but a different payload return ``409 idempotency_conflict``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models.idempotency import IdempotencyRecord

IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_REPLAYED_HEADER = "Idempotency-Replayed"
IDEMPOTENCY_TTL = timedelta(days=30)
IDEMPOTENCY_UNIQUE_CONSTRAINT = "uq_idempotency_record_user_method_endpoint_key"

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{16,200}$")


class InvalidIdempotencyKey(Exception):
    """Raised when an Idempotency-Key header value fails validation."""


class IdempotencyConflict(Exception):
    """Raised when an Idempotency-Key has been used with a different payload."""

    def __init__(
        self,
        *,
        endpoint: str,
        created_at: datetime,
        in_progress: bool = False,
    ) -> None:
        self.endpoint = endpoint
        self.created_at = created_at
        self.in_progress = in_progress
        super().__init__("Idempotency key reused with a different request payload.")


def validate_idempotency_key(key: str) -> str:
    """Validate the idempotency key shape, returning the same key on success.

    The server treats keys as opaque; structure is allowed but never parsed.
    """
    if not isinstance(key, str) or not _KEY_PATTERN.match(key):
        raise InvalidIdempotencyKey(
            "Idempotency-Key must be 16-200 chars from [A-Za-z0-9._:-]."
        )
    return key


def canonical_payload_hash(payload: Any) -> str:
    """Return SHA-256 hex digest of the canonical JSON form of *payload*.

    Canonical form: ``json.dumps(payload, sort_keys=True,
    separators=(",", ":"), ensure_ascii=False)`` hashed as UTF-8 bytes.
    Equivalent objects with reordered keys produce the same hash; any
    change to a value, including timestamps, produces a different hash.
    """
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    """Coerce non-JSON-native values for canonicalization."""
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    return str(value)


@dataclass(frozen=True)
class IdempotencyLookup:
    """Result of looking up an idempotency record before executing a write."""

    record: IdempotencyRecord | None
    same_payload: bool


def _utcnow() -> datetime:
    """Naive UTC 'now', matching the tz-naive ``DateTime`` columns this module
    reads and writes (``IdempotencyRecord.expires_at`` is
    ``DateTime(timezone=False)``). ``datetime.utcnow()`` is deprecated; this is
    the non-deprecated equivalent (aware UTC stripped back to naive) and
    mirrors the ``_utcnow`` helper in ``app/workers/upload_worker.py``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def find_existing_record(
    session: Session,
    *,
    user_id: int,
    request_method: str,
    endpoint: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> IdempotencyRecord | None:
    """Return an unexpired matching idempotency record, or ``None``.

    Expired records are ignored as if the key had never been used; they
    remain on disk until cleanup but never participate in replay or
    conflict checks.
    """
    now = now or _utcnow()
    stmt = (
        select(IdempotencyRecord)
        .where(IdempotencyRecord.user_id == user_id)
        .where(IdempotencyRecord.request_method == request_method)
        .where(IdempotencyRecord.endpoint == endpoint)
        .where(IdempotencyRecord.idempotency_key == idempotency_key)
        .where(IdempotencyRecord.expires_at > now)
    )
    return session.execute(stmt).scalar_one_or_none()


def lookup_or_conflict(
    session: Session,
    *,
    user_id: int,
    request_method: str,
    endpoint: str,
    idempotency_key: str,
    payload_hash: str,
    now: datetime | None = None,
) -> IdempotencyRecord | None:
    """Look up the matching record and raise ``IdempotencyConflict`` on hash mismatch.

    Returns the matching record (replay candidate) if hash matches, or
    ``None`` if no record exists. Caller is responsible for replay
    rendering when a record is returned.
    """
    existing = find_existing_record(
        session,
        user_id=user_id,
        request_method=request_method,
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        now=now,
    )
    if existing is None:
        return None
    if existing.payload_hash != payload_hash:
        raise IdempotencyConflict(
            endpoint=endpoint, created_at=existing.created_at
        )
    return existing


def record_response(
    session: Session,
    *,
    user_id: int,
    request_method: str,
    endpoint: str,
    idempotency_key: str,
    payload_hash: str,
    status_code: int,
    response_body: Any,
    now: datetime | None = None,
) -> IdempotencyRecord:
    """Persist the successful response so future identical requests replay it.

    The record is added to *session* but not flushed; commit is the
    caller's responsibility (typically the route's ``get_write_db``
    dependency). If the route's transaction rolls back, no record is left
    behind — that is the contract for retryable failures.
    """
    now = now or _utcnow()
    record = IdempotencyRecord(
        user_id=user_id,
        request_method=request_method,
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        status_code=status_code,
        response_body_json=response_body,
        expires_at=now + IDEMPOTENCY_TTL,
    )
    session.add(record)
    return record


def delete_expired_records(session: Session, *, now: datetime | None = None) -> int:
    """Delete expired idempotency records; returns the row count.

    No scheduler is wired up — call from a cron job, ad-hoc command, or
    test as needed.
    """
    now = now or _utcnow()
    result = session.execute(
        delete(IdempotencyRecord).where(IdempotencyRecord.expires_at <= now)
    )
    return result.rowcount or 0
