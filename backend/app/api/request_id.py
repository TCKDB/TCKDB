"""Request-ID middleware and context propagation.

Every HTTP request is tagged with an ``X-Request-ID``: a short opaque
token used to correlate access logs, application logs, and error
responses. Two sources are accepted:

- The caller may set ``X-Request-ID`` on the request. If the value is
  safe (allowed characters, length cap) it is propagated unchanged.
- Otherwise the middleware generates a new ``uuid4`` hex string.

The ID is exposed three ways:

- ``request.state.request_id`` for use in route handlers and exception
  handlers.
- A :data:`request_id_var` :class:`contextvars.ContextVar` so the
  logging filter (see :mod:`app.api.logging_config`) can pull it onto
  every :class:`logging.LogRecord` emitted during the request without
  the handler having to thread it explicitly.
- An ``X-Request-ID`` header on the response, so clients (and any
  proxy in front of the API) see the same ID the server logged.

Public error envelopes intentionally do not embed the request ID — the
response header is the contract. See ``backend/app/api/errors.py``.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._\-]+$")

#: Context-local request id, accessible from any code awaited inside a
#: request without having to thread the value through. The default
#: empty string is meaningful: it lets the logging filter emit
#: ``request_id=""`` for log lines outside a request scope (startup,
#: workers) without raising.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def _is_safe_request_id(value: str) -> bool:
    """True if *value* is a safe-to-echo incoming request id."""
    return (
        0 < len(value) <= _MAX_REQUEST_ID_LENGTH
        and bool(_REQUEST_ID_PATTERN.match(value))
    )


def _generate_request_id() -> str:
    """Return a new request id."""
    return uuid.uuid4().hex


def resolve_request_id(incoming: str | None) -> str:
    """Pick the request id to use for the current request.

    Echo a caller-provided id only when it matches a conservative
    pattern and length cap; otherwise mint a fresh one. Garbage in
    headers must never end up in logs or response headers.
    """
    if incoming is not None and _is_safe_request_id(incoming):
        return incoming
    return _generate_request_id()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach an ``X-Request-ID`` to every request and response.

    Installed *outside* :class:`app.api.rate_limit.RateLimitMiddleware`
    so the id is set before any other middleware runs and is therefore
    visible in rate-limit logs and error envelopes.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
