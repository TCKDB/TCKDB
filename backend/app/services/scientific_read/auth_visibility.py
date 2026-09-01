"""Auth-gated field visibility for the scientific read surface.

The scientific read API is otherwise unauthenticated — see
``docs/specs/read_api_mvp.md`` — but a small number of fields are only
meaningful to a caller who can act on the record they identify.
``submission_ref`` is the first: it names the moderation unit a curator
would open to review this record, and it is the answer to "which upload
did this come from?" An anonymous reader gets no benefit from that
answer and, per the owner's decision, should not receive it at all.

This module is the companion to :mod:`internal_ids`, deliberately kept
separate rather than folded in: internal-id visibility is gated by an
``include=`` opt-in *and* a deployment setting, while this gate is keyed
on whether the request resolved an authenticated actor. The two policies
key off different things and may diverge in scope over time, so sharing
one deny-list would eventually force one policy's shape onto the other.

House rule this exists to honour: *absence describes the request, null
describes the data.* An anonymous caller is not told "this record has no
submission" (``submission_ref: null``) — that would be a claim about the
data. It is simply not shown the field at all, exactly the way an
internal id is omitted rather than nulled when the caller has not opted
in (or the deployment forbids it).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)

#: Field names that render only for an authenticated caller. Matched by
#: exact key name, not a suffix rule like ``internal_ids`` uses — there is
#: exactly one entry so far, and a suffix rule would be guessing at a
#: convention that does not exist yet. Extend this set (not the matching
#: rule) the day a second field needs the same treatment.
AUTH_ONLY_KEYS: frozenset[str] = frozenset({"submission_ref"})


def _strip_recursive(value: Any) -> Any:
    """Recurse into nested dicts/lists, removing auth-only keys."""
    if isinstance(value, dict):
        return {
            key: _strip_recursive(nested)
            for key, nested in value.items()
            if key not in AUTH_ONLY_KEYS
        }
    if isinstance(value, list):
        return [_strip_recursive(item) for item in value]
    return value


def strip_auth_only_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop every :data:`AUTH_ONLY_KEYS` member from *payload*, recursively."""
    return _strip_recursive(payload)


def apply_scientific_read_visibility(
    payload: BaseModel,
    *,
    authenticated: bool,
    settings_obj: Any = None,
) -> BaseModel | JSONResponse:
    """Apply internal-id visibility, then auth-only field visibility.

    Routes call this instead of :func:`apply_internal_ids_visibility`
    directly whenever their response carries an auth-gated field
    (currently ``submission_ref``). *authenticated* is ``True`` iff the
    route resolved a non-``None`` actor via
    :func:`app.api.deps.get_optional_current_user`.

    Returns the untouched Pydantic model only when both checks pass
    unmodified (no internal-id stripping needed *and* the caller is
    authenticated); any stripping — either policy — forces a
    ``JSONResponse``, since FastAPI's ``response_model`` machinery has no
    per-request way to omit a field that is declared on the model.
    """
    result = apply_internal_ids_visibility(payload, settings_obj=settings_obj)
    if authenticated:
        return result
    if isinstance(result, JSONResponse):
        data = json.loads(bytes(result.body))
    else:
        data = result.model_dump(mode="json")
    return JSONResponse(strip_auth_only_fields(data))


__all__ = [
    "AUTH_ONLY_KEYS",
    "apply_scientific_read_visibility",
    "strip_auth_only_fields",
]
