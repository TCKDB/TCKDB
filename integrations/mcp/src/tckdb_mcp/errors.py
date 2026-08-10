"""MCP-safe error model and HTTP-status / transport-exception mapping.

Goals:

- Report the code the *server* chose, and fall back to a stable, narrow
  status-derived vocabulary only when the server did not choose one.
- Never leak raw ``httpx`` exception classes.
- Never embed integer DB IDs in user-facing detail (defensive — the
  backend already enforces this, see
  ``docs/integrity-error-response-hardening-spec.md``).

Why the server's code wins
--------------------------
This layer used to derive ``code`` from the HTTP status alone and throw
away the ``code`` the response body carried. The status is a coarse
bucket: ``422`` covers every refusal the wire schemas and the scientific
checks can make, so an agent was told ``invalid_input`` whether its
geometry disagreed with its SMILES (fix the payload and retry) or its
reaction did not balance (the deposit is wrong; abort). Both are
recoverable in different directions, and the whole point of the typed
envelope is that the difference is a field rather than a sentence to
grep. Discarding it left an agent string-matching English — over a
transport whose consumer is a language model, which will do exactly
that, plausibly, and wrongly.

The status-derived value is not lost; it moves to ``status_class``, so a
consumer that only wants "was this my fault or theirs" still has one
field to read, and one that wants to branch on a specific scientific
refusal now can.
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

    :param code: What refused, as specifically as it can be said. The
        server's own code when the response body carried one, otherwise
        the status-derived bucket.
    :param detail: The human sentence, scrubbed.
    :param http_status: The status, or ``None`` for transport failures.
    :param status_class: The status-derived bucket, always populated so a
        consumer that wants the coarse answer never has to re-derive it
        from ``http_status``. Defaults to ``code``, which is right for
        errors raised locally: no server chose anything, so the two are
        the same fact.
    :param context: Structured facts the server attached to the refusal.
        Passed through verbatim — see :func:`_scrub` for why this one is
        deliberately not masked.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        http_status: int | None = None,
        *,
        status_class: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.status_class = status_class or code
        self.context = dict(context or {})

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "http_status": self.http_status,
            "status_class": self.status_class,
            "context": self.context,
        }


def invalid_input(detail: str) -> MCPToolError:
    """Construct a 422-shaped invalid_input error for client-side validation."""
    return MCPToolError("invalid_input", _scrub(detail), http_status=422)


def map_http_status(
    status: int,
    detail: str,
    *,
    code: str | None = None,
    context: dict[str, Any] | None = None,
) -> MCPToolError:
    """Map a server HTTP response into an MCPToolError.

    :param code: The ``code`` field of the server's error envelope. When
        present it *is* the reported code — the server knows which of the
        many refusals a 422 can carry actually fired, and this layer does
        not. Absent (a legacy body, a proxy's own error page, a
        non-JSON response), the status-derived bucket stands in.
    """
    status_class = _HTTP_STATUS_CODES.get(status, "internal_error")
    return MCPToolError(
        code or status_class,
        _scrub(detail) or f"HTTP {status}",
        http_status=status,
        status_class=status_class,
        context=context,
    )


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
    """Stringify and mask anything that looks like a raw DB id.

    Applied to ``detail`` only, never to ``context``. ``detail`` is prose,
    where an id can hide inside a sentence and nothing downstream depends
    on the exact characters. ``context`` is a typed field the server built
    deliberately, whose values are the facts an agent is supposed to act
    on — masking a six-digit number there would corrupt the very thing the
    field exists to carry (a temperature, a count, a molar mass) to defend
    against a leak the server is separately guarded against.
    """
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
