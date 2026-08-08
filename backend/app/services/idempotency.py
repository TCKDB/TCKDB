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
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.idempotency import IdempotencyRecord

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_REPLAYED_HEADER = "Idempotency-Replayed"
#: Tells the client whether the receipt for this write was persisted with
#: the payload. ``recorded`` means a retry with the same key will replay;
#: ``failed`` means it will re-execute. See :func:`write_receipt_isolated`.
IDEMPOTENCY_RECEIPT_HEADER = "Idempotency-Receipt"
IDEMPOTENCY_RECEIPT_REASON_HEADER = "Idempotency-Receipt-Reason"
IDEMPOTENCY_RECEIPT_RECORDED = "recorded"
IDEMPOTENCY_RECEIPT_FAILED = "failed"
IDEMPOTENCY_TTL = timedelta(days=30)
IDEMPOTENCY_UNIQUE_CONSTRAINT = "uq_idempotency_record_user_method_endpoint_key"

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{16,200}$")


class InvalidIdempotencyKey(Exception):
    """Raised when an Idempotency-Key header value fails validation."""


class ReceiptWriteFailed(Exception):
    """The idempotency receipt could not be written — the payload is unharmed.

    Raised by :func:`write_receipt_isolated` when the receipt INSERT fails
    for a reason that is *about the receipt*, not about the write it
    receipts for. The caller's transaction is still alive and still holds
    the payload; the only thing lost is retry-safety for this key.

    A concurrent-duplicate unique violation is deliberately **not** wrapped
    in this exception — see :func:`write_receipt_isolated`.
    """

    def __init__(self, *, reason: str, cause: BaseException) -> None:
        self.reason = reason
        super().__init__(f"Idempotency receipt could not be written: {reason}")
        self.__cause__ = cause


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

    .. warning::

       This is the low-level primitive. Route code must go through
       :func:`write_receipt_isolated`, which confines the INSERT to a
       ``SAVEPOINT`` so a failing receipt cannot roll back the payload.
       Calling this directly puts the receipt in the payload's transaction
       with no isolation — the 2026-08-05 data-loss shape.
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


def _is_idempotency_unique_violation(exc: IntegrityError) -> bool:
    """True when *exc* is the concurrent-duplicate-key race, not a bad receipt."""
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint == IDEMPOTENCY_UNIQUE_CONSTRAINT


def _describe(exc: BaseException) -> str:
    """Short, log-safe reason string for a receipt failure."""
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate:
        return f"{type(exc).__name__}[{sqlstate}]"
    return type(exc).__name__


def _clear_expired_record_in_scope(
    session: Session,
    *,
    user_id: int,
    request_method: str,
    endpoint: str,
    idempotency_key: str,
    now: datetime,
) -> None:
    """Remove an *expired* receipt occupying this uniqueness scope.

    ``find_existing_record`` ignores expired rows, so a key past its TTL is
    treated as never used and the route re-executes the write. The row is
    still on disk though, and the uniqueness scope has no notion of expiry,
    so inserting the new receipt collides with the corpse of the old one.

    Before this call the collision surfaced at ``COMMIT`` — turning a
    documented "expired keys behave as new" into a ``409`` that rolled the
    upload back, whenever the (unscheduled) ``delete_expired_records``
    sweep had not run. Flushing the receipt inside a savepoint moved that
    from commit time to request time, which is how the existing
    ``test_expired_record_treated_as_new`` case caught it: the shared test
    fixture never commits, so the failure had nowhere to show before.

    Only expired rows are cleared. A live row in the same scope still
    collides, and must — that is the concurrent-duplicate ``409``.
    """
    session.execute(
        delete(IdempotencyRecord)
        .where(IdempotencyRecord.user_id == user_id)
        .where(IdempotencyRecord.request_method == request_method)
        .where(IdempotencyRecord.endpoint == endpoint)
        .where(IdempotencyRecord.idempotency_key == idempotency_key)
        .where(IdempotencyRecord.expires_at <= now)
    )


def write_receipt_isolated(
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
    """Write the receipt inside a ``SAVEPOINT`` so it cannot destroy the payload.

    **Why this exists.** On 2026-08-05 an upload completed, ``201 Created``
    was determined and sent, and then the idempotency row failed to
    serialise at commit time. Receipt and payload shared one transaction,
    so the rollback took the species, reaction and calculations with it.
    The client had already been told ``uploaded: 1``. Nothing was stored.
    The trigger was an encoding mismatch, but the shape is
    encoding-independent: *any* commit-time failure confined to the receipt
    destroys the science it receipts for.

    The isolation has two parts, and both matter:

    1. **Flush first.** The session is flushed *before* the savepoint opens,
       so every pending payload row is already INSERTed outside it. Without
       this, anything the route had not yet flushed would be swept into the
       savepoint and rolled back with a failing receipt — the same data loss
       through a subtler door.
    2. **Savepoint around the receipt alone.** The receipt is added and
       flushed inside ``session.begin_nested()``. A failure rolls back to
       the savepoint, leaving the outer transaction alive and still holding
       the payload, and the session usable for the rest of the request.

    On success the savepoint is released and the receipt becomes part of the
    caller's transaction, exactly as before: receipt and payload still commit
    together, and a later rollback still discards both. The replay contract
    is unchanged.

    :raises ReceiptWriteFailed: the receipt could not be written. **The
        payload is intact and the caller should still return success** — see
        ``app.api.idempotency.IdempotencyContext.record``.
    :raises IntegrityError: the receipt collided with the uniqueness scope
        ``(user_id, request_method, endpoint, idempotency_key)``. This is a
        genuine concurrent duplicate, not a receipt malfunction, and is
        re-raised unwrapped so ``app.api.errors`` still maps it to ``409
        idempotency_conflict`` and the duplicate write still rolls back.
        Swallowing it would let both racers store their science under one
        key — the precise thing the mechanism exists to prevent.
    """
    now = now or _utcnow()

    # Part 1: get the payload out of the savepoint's blast radius.
    session.flush()

    # Part 2: the receipt, and only the receipt, inside the savepoint.
    savepoint = session.begin_nested()
    try:
        _clear_expired_record_in_scope(
            session,
            user_id=user_id,
            request_method=request_method,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            now=now,
        )
        record = record_response(
            session,
            user_id=user_id,
            request_method=request_method,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            status_code=status_code,
            response_body=response_body,
            now=now,
        )
        session.flush()
    except Exception as exc:
        # Undo the receipt and nothing else. The outer transaction — and the
        # payload it holds — survives, and the session stays usable.
        savepoint.rollback()

        if isinstance(exc, IntegrityError) and _is_idempotency_unique_violation(exc):
            # A concurrent duplicate, not a broken receipt. Let it out
            # unwrapped so the write still aborts and still returns 409.
            raise

        reason = _describe(exc)
        _log_receipt_failure(
            reason,
            exc,
            user_id=user_id,
            request_method=request_method,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
        )
        raise ReceiptWriteFailed(reason=reason, cause=exc) from exc

    savepoint.commit()
    return record


def _log_receipt_failure(
    reason: str,
    exc: BaseException,
    *,
    user_id: int,
    request_method: str,
    endpoint: str,
    idempotency_key: str,
) -> None:
    """Log a receipt failure loudly enough to diagnose without reading source.

    The 2026-08-05 incident left a bare access-log line; working out what
    had happened meant reading this module. Everything needed to identify
    the affected write and the reason it failed goes in one ERROR record.
    """
    logger.error(
        "Idempotency receipt write FAILED (reason=%s) for %s %s key=%s user_id=%s. "
        "The payload committed and the client was told it succeeded, but this key "
        "will NOT replay: a retry re-executes the write instead of returning the "
        "stored response.",
        reason,
        request_method,
        endpoint,
        idempotency_key,
        user_id,
        exc_info=exc,
    )


def retry_receipt_after_commit(
    session_factory: Callable[[], Session],
    *,
    user_id: int,
    request_method: str,
    endpoint: str,
    idempotency_key: str,
    payload_hash: str,
    status_code: int,
    response_body: Any,
) -> bool:
    """Re-attempt a failed receipt in its own transaction. Returns success.

    Called only after the payload transaction has committed, which is the
    single point at which this is safe. Writing a receipt *before* the
    payload is durable would be far worse than having none: a receipt for a
    write that then rolled back would replay a success response for science
    that does not exist.

    Once the payload is durable a receipt failure is merely a lost retry
    guarantee, and a transient cause (deadlock, connection blip) deserves a
    second attempt in a clean transaction rather than leaving idempotency
    permanently broken for that key. A deterministic cause — the incident's
    unstorable body, for instance — fails again here, and that is fine: this
    runs after the response is sent and never raises into the request path.
    """
    try:
        with session_factory() as retry_session:
            record_response(
                retry_session,
                user_id=user_id,
                request_method=request_method,
                endpoint=endpoint,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                status_code=status_code,
                response_body=response_body,
            )
            retry_session.commit()
    except Exception as exc:  # must never escape the commit path
        logger.error(
            "Idempotency receipt retry FAILED (reason=%s) for %s %s key=%s "
            "user_id=%s. Retry-safety for this key is permanently lost; a "
            "client retry will re-execute the write.",
            _describe(exc),
            request_method,
            endpoint,
            idempotency_key,
            user_id,
            exc_info=exc,
        )
        return False

    logger.warning(
        "Idempotency receipt recovered on retry for %s %s key=%s user_id=%s. "
        "The receipt was written in a separate transaction after the payload "
        "committed; this key replays again.",
        request_method,
        endpoint,
        idempotency_key,
        user_id,
    )
    return True


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
