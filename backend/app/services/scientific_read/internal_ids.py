"""Phase D internal-ID visibility helpers.

The scientific read API surfaces both public refs (``*_ref``) and the
historical integer primary keys (``*_id``, plus a small handful of
non-suffix keys like ``LiteratureSummary.id``). Phase D hides those
integer keys from public responses by default; callers may opt in via
``include=internal_ids``, but the opt-in is only effective when the
deployment allows it (``settings.allow_public_internal_ids``).

See ``docs/specs/internal_ids_visibility_policy.md`` for the full
policy.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.config import settings as default_settings


# ---------------------------------------------------------------------------
# Stripping rules
# ---------------------------------------------------------------------------


# Literal field names known to be internal integer PKs that don't follow
# the ``*_id`` / ``*_ids`` suffix convention. These appear in scientific
# response payloads and must be stripped alongside the suffix-matched
# keys.
_LITERAL_INTERNAL_KEYS: frozenset[str] = frozenset(
    {
        # Top-level ``id`` on LiteratureSummary and ReactionEntrySummary.
        "id",
        # Audit-array record_id (only present with include_review=full;
        # polymorphic, no ref sibling).
        "record_id",
        # User FKs that may surface in extended audit shapes. The current
        # v0 scientific schemas don't expose these, but the deny-list
        # covers them so future additions don't accidentally leak.
        "reviewed_by",
        "created_by",
        "approved_by",
        "rejected_by",
    }
)


# Top-level response keys that hold caller-supplied input and must not
# be recursed into. ``request.filter`` echoes whatever ID/ref the caller
# actually sent — stripping its contents would lose that signal.
_PASSTHROUGH_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"request"})


def is_internal_id_key(key: str) -> bool:
    """Return True iff *key* names an internal integer ID field.

    Match rules:

    - any key whose name ends in ``_id`` or ``_ids`` (covers
      ``species_entry_id``, ``input_geometry_ids``,
      ``ts_opt_calculation_id``, etc.);
    - any key in the explicit literal allow-list (``id``,
      ``record_id``, user-FK keys).

    Public ref keys (``*_ref``) are never matched and stay visible.
    """
    if key in _LITERAL_INTERNAL_KEYS:
        return True
    return key.endswith("_id") or key.endswith("_ids")


# ---------------------------------------------------------------------------
# Visibility policy
# ---------------------------------------------------------------------------


def should_include_internal_ids(
    include: set[str] | list[str] | tuple[str, ...] | None,
    *,
    settings_obj: Any = None,
) -> bool:
    """Return True iff the response should keep internal integer IDs.

    ``include`` is the **resolved** include set surfaced in
    ``request.include`` (post-validation, post-policy-drop). When this
    is ``None`` or does not contain ``"internal_ids"``, the response
    must hide IDs.

    The settings flag ``allow_public_internal_ids`` is the second
    gate: even if ``internal_ids`` survives validation, IDs are only
    restored when the deployment permits it. (In v0 the gate is
    server-wide; once read-side auth lands, this is the natural place
    to layer per-caller context.)
    """
    if include is None:
        return False
    obj = settings_obj if settings_obj is not None else default_settings
    if not getattr(obj, "allow_public_internal_ids", False):
        return False
    return "internal_ids" in set(include)


def filter_internal_ids_from_resolved(
    resolved_includes: set[str],
    *,
    settings_obj: Any = None,
) -> set[str]:
    """Drop ``"internal_ids"`` from *resolved_includes* when policy disallows it.

    Called by services after :func:`validate_includes`. Mutates and
    returns a new ``set``. When ``settings.allow_public_internal_ids``
    is ``False`` the ``internal_ids`` token is silently removed — the
    request layer accepts the token (no 422), but it has no effect on
    the response. The dropped state is reflected in ``request.include``,
    so callers can see when their opt-in did not apply.
    """
    obj = settings_obj if settings_obj is not None else default_settings
    if not getattr(obj, "allow_public_internal_ids", False):
        return resolved_includes - {"internal_ids"}
    return set(resolved_includes)


# ---------------------------------------------------------------------------
# Stripping
# ---------------------------------------------------------------------------


def _strip_recursive(value: Any) -> Any:
    """Recurse into nested dicts/lists, removing internal-ID keys."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if is_internal_id_key(k):
                continue
            out[k] = _strip_recursive(v)
        return out
    if isinstance(value, list):
        return [_strip_recursive(item) for item in value]
    return value


def strip_internal_ids(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip internal-ID keys from a scientific read response payload.

    Walks the payload recursively and drops every key matched by
    :func:`is_internal_id_key`. Public ref fields and scientific
    values are preserved unchanged.

    The ``request`` top-level block is preserved verbatim: request
    echoes mirror caller input, including integer-id filters the
    caller explicitly supplied. Resolving a ref to an integer id never
    surfaces in ``request.filter``, so this preservation does not leak
    resolved internals.
    """
    if not isinstance(payload, dict):
        return _strip_recursive(payload)
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _PASSTHROUGH_TOP_LEVEL_KEYS:
            out[key] = value
            continue
        if is_internal_id_key(key):
            continue
        out[key] = _strip_recursive(value)
    return out


def apply_internal_ids_visibility(
    payload: BaseModel,
    *,
    settings_obj: Any = None,
) -> BaseModel | JSONResponse:
    """Apply the Phase D internal-ID visibility policy to a response.

    Decision rule:

    - If the payload's resolved include set (``request.include``)
      contains ``"internal_ids"`` **and** the deployment allows it,
      return the Pydantic model unchanged so FastAPI serializes the
      full id-bearing shape via ``response_model``.
    - Otherwise, dump the model to JSON, strip every internal-ID key
      via :func:`strip_internal_ids`, and return a ``JSONResponse``
      with the cleaned dict. The ``request`` echo is preserved
      verbatim so caller-supplied integer-id filters remain visible.

    Routes call this at the return boundary; services do not need to
    know about it.
    """
    resolved_includes = _extract_resolved_includes(payload)
    if should_include_internal_ids(
        resolved_includes, settings_obj=settings_obj
    ):
        return payload
    raw = payload.model_dump(mode="json")
    return JSONResponse(strip_internal_ids(raw))


def _extract_resolved_includes(payload: BaseModel) -> set[str]:
    """Pull the resolved include set off a scientific response model."""
    request_echo = getattr(payload, "request", None)
    if request_echo is None:
        return set()
    include = getattr(request_echo, "include", None)
    if include is None:
        return set()
    return set(include)


__all__ = [
    "is_internal_id_key",
    "should_include_internal_ids",
    "filter_internal_ids_from_resolved",
    "strip_internal_ids",
    "apply_internal_ids_visibility",
]
