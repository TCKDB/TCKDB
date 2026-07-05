"""FastAPI integration for idempotency-keyed write endpoints.

Provides a route-level dependency (``idempotency_dependency``) that:

1. Reads the optional ``Idempotency-Key`` header.
2. Validates it (raising ``InvalidIdempotencyKey`` on bad shape).
3. Hashes the canonical JSON request body.
4. Looks up an existing record on the route's write session.
5. Returns an ``IdempotencyContext`` the route uses to (a) replay an
   existing response or (b) record a fresh response after the write.

Concurrent in-flight duplicate requests are *not* protected by an
explicit advisory lock or ``idempotency_in_progress`` placeholder for
v0. The unique constraint
``uq_idempotency_record_user_method_endpoint_key`` plus the integrity
error handler in :mod:`app.api.errors` is what catches a race: the
losing committer surfaces ``409 idempotency_conflict`` via constraint-
name mapping. This is the explicitly-allowed v0 behavior in
``docs/specs/upload-idempotency-key-spec.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_write_db
from app.db.models.app_user import AppUser
from app.db.models.idempotency import IdempotencyRecord
from app.services.idempotency import (
    IDEMPOTENCY_HEADER,
    IDEMPOTENCY_REPLAYED_HEADER,
    canonical_payload_hash,
    lookup_or_conflict,
    record_response,
    validate_idempotency_key,
)


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
        returning. The record commits atomically with the route's write
        because both share the ``get_write_db`` session.
        """
        if not self.enabled or self._recorded:
            return
        assert self.key is not None
        assert self.payload_hash is not None
        assert self.endpoint is not None
        assert self.method is not None
        assert self.user_id is not None
        record_response(
            session,
            user_id=self.user_id,
            request_method=self.method,
            endpoint=self.endpoint,
            idempotency_key=self.key,
            payload_hash=self.payload_hash,
            status_code=status_code,
            response_body=body,
        )
        self._recorded = True


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
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idempotency_key: str | None = Header(None, alias=IDEMPOTENCY_HEADER),
) -> IdempotencyContext:
    """FastAPI dependency that prepares the per-request idempotency context.

    No-key requests get a disabled context and behave exactly as before.
    Keyed requests have their key validated, body canonically hashed, and
    any prior matching record loaded for replay; a hash mismatch raises
    ``IdempotencyConflict`` before the route body ever runs.
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
    )
