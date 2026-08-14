"""FastAPI integration for idempotency-keyed write endpoints.

Provides a route-level dependency (``idempotency_dependency``) that:

1. Reads the optional ``Idempotency-Key`` header.
2. Validates it (raising ``InvalidIdempotencyKey`` on bad shape).
3. Hashes the canonical JSON request body.
4. Looks up an existing record on the route's write session.
5. Returns an ``IdempotencyContext`` the route uses to (a) replay an
   existing response or (b) record a fresh response after the write.

Concurrent in-flight duplicate requests are *not* protected by an
explicit advisory lock or ``idempotency_in_progress`` placeholder. The
unique constraint ``uq_idempotency_record_user_method_endpoint_key``
plus the integrity error handler in :mod:`app.api.errors` is what
catches a race: the losing committer surfaces ``409
idempotency_conflict`` via constraint-name mapping.

Whether that is a gap or a decision
-----------------------------------
This paragraph used to end "for v0", and "deliberate for v0" and
"planned for v1" are not the same claim — a reader looking for which
one this was could not tell, which is the only reason the distinction
is spelled out here at all.

It is a decision. ``docs/specs/upload-idempotency-key-spec.md`` lists
``idempotency_in_progress`` under *Optional* and says "Only implement
``idempotency_in_progress`` if needed by the chosen approach". It was
never a milestone to reach; it was a code that some approaches need and
others do not, and the approach chosen here — one unique constraint,
one integrity handler — does not. Nothing scheduled sets the flag.

The consequence, recorded because it is a published contract. The
``in_progress`` ternary in
:func:`app.api.errors._idempotency_conflict_handler` has one live arm:
``IdempotencyConflict.in_progress`` defaults to ``False`` and the one
construction site in :mod:`app.services.idempotency` never passes it,
so ``idempotency_in_progress`` cannot reach a response body. It is
catalogued as :attr:`app.api.code_catalogue.Reach.guard` and is not
generated into the client's ``RejectionCode``, because an enum member a
caller can import and branch on but never receive is a lie in the
contract.

The parameter and the ternary stay. They are the two lines that make
the state sayable the day an advisory lock is added, and the catalogue
entry is what tells whoever adds it that the code already exists rather
than inventing a second spelling for it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.api.deps import SessionLocal, get_current_user, get_write_db
from app.db.models.app_user import AppUser
from app.db.models.idempotency import IdempotencyRecord
from app.services.idempotency import (
    IDEMPOTENCY_HEADER,
    IDEMPOTENCY_RECEIPT_FAILED,
    IDEMPOTENCY_RECEIPT_HEADER,
    IDEMPOTENCY_RECEIPT_REASON_HEADER,
    IDEMPOTENCY_RECEIPT_RECORDED,
    IDEMPOTENCY_REPLAYED_HEADER,
    ReceiptWriteFailed,
    canonical_payload_hash,
    lookup_or_conflict,
    retry_receipt_after_commit,
    validate_idempotency_key,
    write_receipt_isolated,
)

logger = logging.getLogger(__name__)


@dataclass
class IdempotencyContext:
    """Per-request idempotency state shared between route and helpers."""

    enabled: bool
    key: str | None = None
    payload_hash: str | None = None
    endpoint: str | None = None
    method: str | None = None
    user_id: int | None = None
    existing: IdempotencyRecord | None = None
    response: Response | None = None
    #: ``None`` until :meth:`record` runs, then ``"recorded"`` or ``"failed"``.
    receipt_status: str | None = field(default=None, init=False)
    #: Short reason string when ``receipt_status == "failed"``.
    receipt_failure_reason: str | None = field(default=None, init=False)
    _recorded: bool = field(default=False, init=False)

    @classmethod
    def disabled(cls) -> "IdempotencyContext":
        return cls(enabled=False)

    def maybe_replay(self) -> JSONResponse | None:
        """Return a replay response if a matching record was found, else None."""
        if not self.enabled or self.existing is None:
            return None
        return JSONResponse(
            status_code=self.existing.status_code,
            content=self.existing.response_body_json,
            headers={IDEMPOTENCY_REPLAYED_HEADER: "true"},
        )

    def record(
        self,
        session: Session,
        *,
        status_code: int,
        body: Any,
    ) -> None:
        """Persist the successful response onto *session* for future replay.

        No-ops when idempotency is disabled (no header) or already recorded.
        Caller must invoke this after the route's write succeeds and before
        returning.

        On the success path the receipt still commits atomically with the
        route's write — both share the ``get_write_db`` session — so the
        replay contract is untouched.

        **When the receipt cannot be written**, the payload survives (see
        :func:`app.services.idempotency.write_receipt_isolated`) and this
        method does not raise. The route returns its real success status,
        because the science genuinely was stored and saying otherwise would
        be a lie in the more dangerous direction: a ``5xx`` invites the
        client to retry, and with no receipt on file that retry re-executes
        the upload and duplicates the science. What the client gets instead
        is the truthful status plus an explicit
        ``Idempotency-Receipt: failed`` header naming the reason, so a
        caller that depends on exactly-once knows this key will not replay.
        Returning a bare success with no signal is the pre-fix behaviour and
        is exactly what kept the 2026-08-05 incident invisible.

        A concurrent-duplicate ``IntegrityError`` still propagates, so a key
        raced by two in-flight requests still yields ``409``.
        """
        if not self.enabled or self._recorded:
            return
        assert self.key is not None
        assert self.payload_hash is not None
        assert self.endpoint is not None
        assert self.method is not None
        assert self.user_id is not None

        # Set before attempting the write: a second call in the same request
        # must not re-attempt a receipt that already failed.
        self._recorded = True

        try:
            write_receipt_isolated(
                session,
                user_id=self.user_id,
                request_method=self.method,
                endpoint=self.endpoint,
                idempotency_key=self.key,
                payload_hash=self.payload_hash,
                status_code=status_code,
                response_body=body,
            )
        except ReceiptWriteFailed as exc:
            self.receipt_status = IDEMPOTENCY_RECEIPT_FAILED
            self.receipt_failure_reason = exc.reason
            logger.error(
                "Returning %s for %s %s with an UNRECORDED idempotency receipt "
                "(key=%s user_id=%s reason=%s). The write itself succeeded; only "
                "retry-safety was lost.",
                status_code,
                self.method,
                self.endpoint,
                self.key,
                self.user_id,
                exc.reason,
            )
            self._apply_receipt_headers()
            self._schedule_retry_after_commit(
                session, status_code=status_code, body=body
            )
            return

        self.receipt_status = IDEMPOTENCY_RECEIPT_RECORDED
        self._apply_receipt_headers()

    def _apply_receipt_headers(self) -> None:
        """Surface the receipt outcome on the response.

        A header rather than a body field: it applies uniformly to every
        keyed write endpoint without changing a single response schema, and
        so cannot drift as new upload routes are added.
        """
        if self.response is None or self.receipt_status is None:
            return
        self.response.headers[IDEMPOTENCY_RECEIPT_HEADER] = self.receipt_status
        if self.receipt_failure_reason is not None:
            self.response.headers[IDEMPOTENCY_RECEIPT_REASON_HEADER] = (
                self.receipt_failure_reason
            )

    def _schedule_retry_after_commit(
        self, session: Session, *, status_code: int, body: Any
    ) -> None:
        """Re-attempt the receipt once the payload transaction is durable.

        ``after_commit`` is the earliest safe moment: before it, the payload
        might still roll back, and a receipt for a write that never happened
        would replay a success response for science that does not exist —
        worse than no receipt at all.

        The retry binds to the *engine*, never to the caller's
        ``Connection``. That distinction is the whole point: a session that
        joined the caller's connection would share its transaction, and
        rolling back a failed retry could then roll back the write it was
        supposed to be protecting — reintroducing the bug through the
        repair. Binding to the engine takes a separate connection with a
        separate transaction, so the retry cannot reach the payload at all.

        Best-effort by construction: a failure is logged and swallowed,
        never raised into the commit path.
        """
        assert self.key is not None
        assert self.payload_hash is not None
        assert self.endpoint is not None
        assert self.method is not None
        assert self.user_id is not None

        bind = session.get_bind()
        # ``Engine.engine`` is the engine itself; ``Connection.engine`` is the
        # engine behind it. Either way this resolves to something that hands
        # out a *new* connection.
        engine = getattr(bind, "engine", None)
        session_factory = (
            (lambda: Session(bind=engine)) if engine is not None else SessionLocal
        )
        params = {
            "user_id": self.user_id,
            "request_method": self.method,
            "endpoint": self.endpoint,
            "idempotency_key": self.key,
            "payload_hash": self.payload_hash,
            "status_code": status_code,
            "response_body": body,
        }

        @event.listens_for(session, "after_commit", once=True)
        def _retry(_session: Session) -> None:  # pragma: no cover - see tests
            retry_receipt_after_commit(session_factory, **params)


def _endpoint_key(request: Request) -> str:
    """Stable endpoint identity for the idempotency record's scope.

    Uses the concrete request path (``request.url.path``), e.g.
    ``/api/v1/uploads/conformers`` or
    ``/api/v1/uploads/calculations/10/artifacts``. This differentiates the
    idempotency uniqueness scope
    ``(user_id, request_method, endpoint, idempotency_key)`` per target
    resource, so a client reusing one idempotency key against different
    resource targets (``POST /calculations/10/artifacts`` vs
    ``/calculations/20/artifacts``) is correctly treated as two distinct
    writes.

    Previously derived from ``request.scope["route"].path`` on the assumption
    that the route template equalled the full concrete path. Newer Starlette
    returns the leaf-relative template for nested routers (``/conformers``
    instead of ``/api/v1/uploads/conformers``), so the concrete URL path is
    used directly — version-robust and identical to the intended behavior.
    """
    return request.url.path


async def idempotency_dependency(
    request: Request,
    response: Response,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idempotency_key: str | None = Header(None, alias=IDEMPOTENCY_HEADER),
) -> IdempotencyContext:
    """FastAPI dependency that prepares the per-request idempotency context.

    No-key requests get a disabled context and behave exactly as before.
    Keyed requests have their key validated, body canonically hashed, and
    any prior matching record loaded for replay; a hash mismatch raises
    ``IdempotencyConflict`` before the route body ever runs.

    ``response`` is FastAPI's mutable response stub. Headers set on it in a
    dependency are merged into whatever the route returns, which is how
    :meth:`IdempotencyContext.record` reports the receipt outcome without
    every upload route having to thread it through its own response model.
    """
    if idempotency_key is None:
        return IdempotencyContext.disabled()

    validated_key = validate_idempotency_key(idempotency_key)
    body_bytes = await request.body()
    payload_obj: Any
    if not body_bytes:
        payload_obj = None
    else:
        payload_obj = json.loads(body_bytes.decode("utf-8"))
    payload_hash = canonical_payload_hash(payload_obj)
    endpoint = _endpoint_key(request)
    method = request.method

    existing = lookup_or_conflict(
        session,
        user_id=current_user.id,
        request_method=method,
        endpoint=endpoint,
        idempotency_key=validated_key,
        payload_hash=payload_hash,
    )

    return IdempotencyContext(
        enabled=True,
        key=validated_key,
        payload_hash=payload_hash,
        endpoint=endpoint,
        method=method,
        user_id=current_user.id,
        existing=existing,
        response=response,
    )
