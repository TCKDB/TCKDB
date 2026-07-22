"""Machine-consumer error-envelope helpers.

The public interface is deliberately small: domain code may raise
``CodedValueError`` when it has structured context, while exception handlers
use :func:`error_envelope` for both new and legacy errors.  Legacy
``"code: message"`` details remain valid and are promoted into the additive
top-level ``code`` field.
"""

from __future__ import annotations

import math
import re
from typing import Any

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


class CodedValueError(ValueError):
    """A 422 domain error with a stable code and machine-readable context."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.detail = detail
        self.context = dict(context or {})
        super().__init__(f"{code}: {detail}")


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


def validation_detail_code(detail: object, *, fallback: str) -> str:
    """Promote one unambiguous nested ``snake_case:`` validation code."""

    # Pydantic/FastAPI expose the independent validation failures as the
    # outer list. Even if two failures happen to carry the same embedded
    # code, promoting that code would hide the fact that the request failed
    # in more than one place.
    if isinstance(detail, (list, tuple)) and len(detail) != 1:
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
    "CodedValueError",
    "detail_code",
    "error_envelope",
    "reject_unsupported_filters",
    "validation_detail_code",
]
