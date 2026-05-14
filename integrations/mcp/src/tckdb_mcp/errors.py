"""MCP-safe error model and HTTP-status / transport-exception mapping.

Goals:

- Stable, narrow vocabulary of agent-facing error codes.
- Never leak raw ``httpx`` exception classes.
- Never embed integer DB IDs in user-facing detail (defensive — the
  backend already enforces this, see
  ``docs/integrity-error-response-hardening-spec.md``).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

# Status-to-code mapping. Kept as a flat dict so it's easy to scan and
# easy to extend with new server status codes (e.g. 429 rate_limited).
_HTTP_STATUS_CODES: dict[int, str] = {
    400: "invalid_input",
    401: "auth_required",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "invalid_input",
    429: "rate_limited",
    500: "service_unavailable",
    502: "service_unavailable",
    503: "service_unavailable",
    504: "service_unavailable",
}

# Crude defensive scrubber. The server already strips DB ids from
# `detail`, but if a future regression slips one through we still
# want the agent to see ``<id>`` rather than a raw integer.
_BARE_LARGE_INT = re.compile(r"\b\d{6,}\b")


class MCPToolError(Exception):
    """Stable, MCP-safe error envelope.

    Raised by tools and the HTTP wrapper; the MCP dispatcher renders
    these as ``{"error": {...}}`` text content for the agent.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        http_status: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.http_status = http_status

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "http_status": self.http_status,
        }


def invalid_input(detail: str) -> MCPToolError:
    """Construct a 422-shaped invalid_input error for client-side validation."""
    return MCPToolError("invalid_input", _scrub(detail), http_status=422)


def map_http_status(status: int, detail: str) -> MCPToolError:
    """Map a server HTTP status into an MCPToolError."""
    code = _HTTP_STATUS_CODES.get(status, "internal_error")
    return MCPToolError(code, _scrub(detail) or f"HTTP {status}", http_status=status)


def map_httpx_exception(exc: BaseException) -> MCPToolError:
    """Map a transport-layer exception into an MCPToolError.

    Timeouts surface as ``timeout``; all other transport failures
    (DNS, connection refused, TLS, read errors) collapse to
    ``network_error``. The raw exception class name is included in
    detail to aid debugging without leaking internals.
    """
    if isinstance(exc, httpx.TimeoutException):
        return MCPToolError("timeout", "request timed out", http_status=None)
    if isinstance(exc, httpx.TransportError):
        return MCPToolError(
            "network_error",
            f"network failure ({exc.__class__.__name__})",
            http_status=None,
        )
    return MCPToolError(
        "internal_error",
        f"unexpected transport error ({exc.__class__.__name__})",
        http_status=None,
    )


def _scrub(detail: Any) -> str:
    """Stringify and mask anything that looks like a raw DB id."""
    if detail is None:
        return ""
    if not isinstance(detail, str):
        detail = str(detail)
    return _BARE_LARGE_INT.sub("<id>", detail)


__all__ = [
    "MCPToolError",
    "invalid_input",
    "map_http_status",
    "map_httpx_exception",
]
