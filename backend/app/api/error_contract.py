"""Machine-consumer error-envelope helpers.

The public interface is deliberately small: domain code may raise
``CodedValueError`` when it has structured context, while exception handlers
use :func:`error_envelope` for both new and legacy errors.  Legacy
``"code: message"`` details remain valid and are promoted into the additive
top-level ``code`` field.
"""

from __future__ import annotations

from typing import Any


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


def error_envelope(
    detail: object,
    *,
    code: str | None = None,
    context: dict[str, Any] | None = None,
    fallback_code: str,
) -> dict[str, Any]:
    """Return the additive ``code`` / ``detail`` / ``context`` envelope."""

    return {
        "code": code or detail_code(detail, fallback=fallback_code),
        "detail": detail,
        "context": dict(context or {}),
    }


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
]
