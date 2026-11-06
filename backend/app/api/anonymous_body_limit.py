"""Body size allowance for callers with no valid credential, enforced before the body is read.

FastAPI reads and parses a whole request body before dependencies such as authentication run, so an unauthenticated
``POST`` to any body-taking route used to be buffered and parsed in full before its 401. This is a pure ASGI
middleware, like :mod:`app.api.bundle_limits` and :mod:`app.api.export_limits`: for ``POST``/``PUT``/``PATCH`` under
``/api/`` it refuses a body over ``settings.anonymous_max_body_bytes`` (``413 anonymous_body_too_large``) unless the
request carries a valid credential.

* **Content-Length declared:** a body at or under the allowance is passed through with no database work. A larger
  one costs one credential lookup, made before a byte is read; a valid credential lifts the allowance.
* **No Content-Length (chunked):** bytes are counted as they stream. The lookup is made once, the moment the count
  passes the allowance, and a caller without a valid credential is refused there; the rest is never read.

A credential is what :mod:`app.api.deps` accepts: an ``X-API-Key`` that is known and unrevoked, or, when no key is
sent, a ``tckdb_session`` cookie that is known, unrevoked and unexpired. The validation is the services' own
(:func:`app.services.auth.authenticate_api_key`, :func:`app.services.auth.resolve_session`), so the two cannot
disagree. A key that is sent and invalid or revoked is no credential, and, as in the dependencies, does not fall back
to the cookie.

``POST .../networks/{ref}/kinetics/export-selected`` is not subject to the allowance: it legitimately takes a
manifest larger than it and keeps its own cap and coded error (:mod:`app.api.export_limits`). Which error wins
elsewhere: this middleware wraps the bundle and export middlewares, so a caller without a credential is told to
authenticate before it is told a bundle is too large; a caller with one reaches the route's own caps unchanged.
"""

from __future__ import annotations

import re

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.config import settings
from app.api.error_contract import error_envelope
from app.services.auth import (
    API_KEY_HEADER,
    SESSION_COOKIE_NAME,
    authenticate_api_key,
    resolve_session,
)

CODE_ANONYMOUS_BODY_TOO_LARGE = "anonymous_body_too_large"
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
API_PREFIX = "/api/"
#: Routes that keep their own, larger, pre-parse cap and coded error.
OWN_CAP_PATHS = re.compile(r"^/api/v1/scientific/networks/[^/]+/kinetics/export-selected$")


def has_valid_credential(scope: Scope) -> bool:
    """True when the request carries a credential :mod:`app.api.deps` would accept. One indexed lookup."""
    # Late import: ``app.api.deps`` builds the engine at import, and the test harness rebinds ``SessionLocal`` in place.
    from app.api.deps import SessionLocal

    request = Request(scope)
    api_key = request.headers.get(API_KEY_HEADER)
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not api_key and not cookie:
        return False
    with SessionLocal() as session:
        if api_key:
            return authenticate_api_key(session, api_key, on_authenticated=None) is not None
        return resolve_session(session, cookie) is not None


def _refusal(limit: int, given: int | None) -> JSONResponse:
    context: dict[str, int] = {"max_bytes": limit}
    if given is not None:
        context["given_bytes"] = given
    return JSONResponse(
        status_code=413,
        content=error_envelope(
            f"A request without a valid credential may be at most {limit:,} bytes. "
            "Authenticate with an API key or a session to send a larger one.",
            code=CODE_ANONYMOUS_BODY_TOO_LARGE,
            context=context,
            fallback_code=CODE_ANONYMOUS_BODY_TOO_LARGE,
        ),
        headers={"Connection": "close"},
    )


class AnonymousBodyLimitMiddleware:
    """Refuse a large request body from a caller with no valid credential, before it is read."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = settings.anonymous_max_body_bytes
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope["method"] not in BODY_METHODS
            or not path.startswith(API_PREFIX)
            or not limit
            or OWN_CAP_PATHS.match(path)
        ):
            await self.app(scope, receive, send)
            return

        declared: int | None = None
        for name, value in scope["headers"]:
            if name == b"content-length":
                declared = int(value) if value.isdigit() else None
                break

        verdict: bool | None = None  # the credential lookup, made at most once

        async def credentialed() -> bool:
            nonlocal verdict
            if verdict is None:
                verdict = await run_in_threadpool(has_valid_credential, scope)
            return verdict

        if declared is not None and declared > limit:
            if await credentialed():
                await self.app(scope, receive, send)
                return
            await _refusal(limit, declared)(scope, receive, send)
            return

        seen = 0
        refused = False

        async def counting_receive() -> Message:
            nonlocal seen, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request" and verdict is not True:
                seen += len(message.get("body", b""))
                if seen > limit and not await credentialed():
                    refused = True
                    await _refusal(limit, None)(scope, receive, send)
                    # The app is still waiting on its body: tell it the client is gone, so it stops reading.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not refused:  # the app's reply to a request we already refused
                await send(message)

        await self.app(scope, counting_receive, guarded_send)


__all__ = [
    "BODY_METHODS",
    "CODE_ANONYMOUS_BODY_TOO_LARGE",
    "OWN_CAP_PATHS",
    "AnonymousBodyLimitMiddleware",
    "has_valid_credential",
]
