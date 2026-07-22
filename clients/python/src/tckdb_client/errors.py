"""Structured exception hierarchy for the TCKDB client.

Mirrors the server's error envelope. Every HTTP error carries the raw
status code, parsed JSON (if any), the error ``code`` field provided by
the server or recovered from a legacy detail prefix, and the original
response headers — enough state for callers to make policy decisions
(retry, surface to user, abort) without re-parsing the response.
"""

from __future__ import annotations

from typing import Any, Mapping


class TCKDBError(Exception):
    """Base class for every error raised by the client."""


class TCKDBConnectionError(TCKDBError):
    """Network failure or timeout — request never produced an HTTP status."""


class TCKDBPaginationError(TCKDBError):
    """A paginated response was malformed or could not advance safely."""


class TCKDBHTTPError(TCKDBError):
    """Server responded with a non-success status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        detail: object | None = None,
        response_json: Any = None,
        response_text: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.response_json = response_json
        self.response_text = response_text
        self.headers = dict(headers) if headers is not None else None


class TCKDBAuthenticationError(TCKDBHTTPError):
    """401 — missing or invalid API key."""


class TCKDBForbiddenError(TCKDBHTTPError):
    """403 — authenticated but not permitted."""


class TCKDBValidationError(TCKDBHTTPError):
    """422 — payload failed server-side validation."""


class TCKDBConflictError(TCKDBHTTPError):
    """409 — generic conflict (unique constraint, state conflict, etc.)."""


class TCKDBIdempotencyConflictError(TCKDBConflictError):
    """409 with ``code=idempotency_conflict`` — same key, different payload."""


__all__ = [
    "TCKDBError",
    "TCKDBConnectionError",
    "TCKDBPaginationError",
    "TCKDBHTTPError",
    "TCKDBAuthenticationError",
    "TCKDBForbiddenError",
    "TCKDBValidationError",
    "TCKDBConflictError",
    "TCKDBIdempotencyConflictError",
]
