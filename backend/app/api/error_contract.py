"""Machine-consumer error-envelope helpers.

The public interface is deliberately small: domain code may raise
``CodedValueError`` when it has structured context, while exception handlers
use :func:`error_envelope` for both new and legacy errors.  Legacy
``"code: message"`` details remain valid and are promoted into the additive
top-level ``code`` field.

Three ways a ``code`` can be found, in descending order of trust
---------------------------------------------------------------
1. **The exception says so.** A :class:`CodedValidationError` — raised
   directly by service code, or raised inside a Pydantic validator and
   preserved by Pydantic in ``errors()[i]["ctx"]["error"]`` — carries
   ``.code`` as an attribute. Nothing is parsed. This is the mechanism
   every scientific refusal uses, and the only one a new one should.
2. **A legacy ``"code: message"`` detail**, promoted by :func:`detail_code`.
3. **A nested ``code:`` inside a framework-generated validation message**,
   promoted by :func:`validation_detail_code`.

(2) and (3) read English, and reading English is how a code silently stops
being reported when somebody rewords a sentence — which is exactly what had
happened to the parenthesised convention the scientific checks used to use
(``"... (reaction_mass_balance_failed)."``): the pattern below requires a
colon, so those codes matched nothing and every chemistry refusal reached
its client as the generic ``validation_error``. They are kept because
existing details are a published surface, not because they are a good idea.
"""

from __future__ import annotations

import math
import re
from typing import Any

from tckdb_schemas.coded_error import CodedValidationError

_NESTED_CODE_PATTERN = re.compile(r"(?<![a-z0-9_])([a-z][a-z0-9_]*_[a-z0-9_]+): ")


def _json_safe(obj: Any) -> Any:
    """Recursively replace non-finite floats with their string form.

    A validation error echoes the offending input, which for the
    ``allow_inf_nan=False`` fields may itself be ``nan``/``inf``. Left as a
    float that value would break ``json.dumps(allow_nan=False)`` when the
    response is rendered, cascading the request-validation error into the
    generic ``ValueError`` handler and mislabelling its ``code``. Coercing
    to ``"nan"``/``"inf"`` keeps the echo readable and the envelope
    serialisable.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else str(obj)
    if isinstance(obj, dict):
        return {key: _json_safe(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(value) for value in obj]
    return obj


class CodedValueError(CodedValidationError):
    """A 422 domain error with a stable code and machine-readable context.

    The backend-side name for :class:`~tckdb_schemas.coded_error.CodedValidationError`,
    which lives in the wire package so that a schema-level check — forbidden
    from importing anything under ``app`` — can raise the same thing. Both
    are caught by the same handler and reported the same way; use whichever
    one the module you are in can import.
    """


def detail_code(detail: object, *, fallback: str) -> str:
    """Extract a legacy ``code: message`` prefix or return *fallback*."""

    if isinstance(detail, dict):
        nested = detail.get("code")
        if isinstance(nested, str) and nested:
            return nested
    if isinstance(detail, str):
        prefix, separator, _tail = detail.partition(": ")
        if separator and prefix and all(ch.islower() or ch.isdigit() or ch == "_" for ch in prefix):
            return prefix
    return fallback


def _declared_errors(detail: object) -> list[CodedValidationError]:
    """The coded exceptions Pydantic preserved inside a validation detail.

    A ``ValueError`` raised inside a validator reaches ``errors()`` as
    ``{"type": "value_error", "msg": "Value error, <prose>", "ctx":
    {"error": <the exception>}}``. When that exception is a
    :class:`CodedValidationError` its code and context are attributes of
    it, so what a client receives is what the check declared — not
    whatever survives in the sentence. This is what lets a refusal gain a
    code without its message moving by a single byte.
    """

    found: list[CodedValidationError] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            context = value.get("ctx")
            if isinstance(context, dict):
                error = context.get("error")
                if isinstance(error, CodedValidationError) and error.code:
                    found.append(error)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    visit(detail)
    return found


def validation_detail_context(detail: object) -> dict[str, Any]:
    """Structured facts from the coded error behind a validation failure.

    A check raised from service code hands its ``context`` straight to the
    handler; one raised inside a Pydantic validator has it buried in the
    error list, where the only way to find the two element counts that
    disagreed is to parse the sentence that named them. Lifting it to the
    envelope's own ``context`` puts both kinds of refusal in the same
    place, which is what makes "read ``context``, never ``detail``"
    advice a client can actually follow.

    Empty unless exactly one code was promoted, for the same reason
    :func:`validation_detail_code` falls back there: facts attached to a
    code the envelope is not reporting would be facts about nothing.
    """

    errors = _declared_errors(detail)
    if len({error.code for error in errors}) != 1:
        return {}
    merged: dict[str, Any] = {}
    for error in errors:
        merged.update(error.context)
    return merged


def validation_detail_code(detail: object, *, fallback: str) -> str:
    """Promote one unambiguous validation code.

    Prefers a code the raising exception *declared* (see
    :func:`_declared_codes`) over one spelled inside a message.
    """

    # Pydantic/FastAPI expose the independent validation failures as the
    # outer list. Even if two failures happen to carry the same embedded
    # code, promoting that code would hide the fact that the request failed
    # in more than one place.
    if isinstance(detail, (list, tuple)) and len(detail) != 1:
        return fallback

    declared = {error.code for error in _declared_errors(detail)}
    if len(declared) == 1:
        return declared.pop()
    if declared:
        # More than one distinct declared code in a single failure means the
        # refusal is genuinely about more than one thing; naming one of them
        # would be a lie about which.
        return fallback

    candidates: set[str] = set()

    def collect_message(value: object) -> None:
        if isinstance(value, BaseException):
            value = str(value)
        if isinstance(value, str):
            candidates.update(_NESTED_CODE_PATTERN.findall(value))

    def collect(value: object) -> None:
        if isinstance(value, dict):
            # Only inspect framework-generated validation messages and their
            # structured exception context. In particular, never inspect the
            # caller-controlled ``input`` value included by Pydantic.
            collect_message(value.get("msg"))
            context = value.get("ctx")
            if isinstance(context, dict):
                collect_message(context.get("error"))
                collect_message(context.get("code"))
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)
        else:
            collect_message(value)

    collect(detail)
    if len(candidates) == 1:
        return candidates.pop()
    return fallback


def error_envelope(
    detail: object,
    *,
    code: str | None = None,
    context: dict[str, Any] | None = None,
    fallback_code: str,
) -> dict[str, Any]:
    """Return the additive ``code`` / ``detail`` / ``context`` envelope.

    The envelope is deep-sanitised so any non-finite float echoed from the
    offending request cannot break JSON rendering (see :func:`_json_safe`).
    """

    return _json_safe(
        {
            "code": code or detail_code(detail, fallback=fallback_code),
            "detail": detail,
            "context": dict(context or {}),
        }
    )


def reject_unsupported_filters(
    supplied: dict[str, object],
    *,
    endpoint: str,
) -> None:
    """Fail closed when a caller supplies a declared but unsupported filter."""

    names = sorted(name for name, value in supplied.items() if value is not None)
    if not names:
        return
    raise CodedValueError(
        "unsupported_filter",
        f"filter(s) {names!r} are not supported by {endpoint}",
        context={"endpoint": endpoint, "filters": names},
    )


__all__ = [
    "CodedValidationError",
    "CodedValueError",
    "detail_code",
    "error_envelope",
    "reject_unsupported_filters",
    "validation_detail_code",
    "validation_detail_context",
]
