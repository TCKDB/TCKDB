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


class TCKDBUnexpectedResponseError(TCKDBHTTPError):
    """A success status whose body is not what the endpoint returns.

    Raised when a JSON endpoint answers 2xx with a body that does not
    parse as JSON, or an export endpoint answers 2xx with an HTML page.
    The usual cause is a ``base_url`` that points at the site root
    (``https://host``) instead of the API root (``https://host/api/v1``):
    the web app's single-page fallback then answers every path with its
    ``index.html`` and a 200. Before this existed the client handed that
    HTML back as the response data, and the caller failed later with an
    unrelated ``TypeError``.

    A subclass of :class:`TCKDBHTTPError` so every existing
    ``except TCKDBHTTPError`` handler already catches it. ``code`` stays
    ``None``: it is reserved for codes the *server* sent, and no TCKDB
    server sent this body.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        content_type: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.url = url
        self.content_type = content_type


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
    "TCKDBUnexpectedResponseError",
    "TCKDBAuthenticationError",
    "TCKDBForbiddenError",
    "TCKDBValidationError",
    "TCKDBConflictError",
    "TCKDBIdempotencyConflictError",
]
