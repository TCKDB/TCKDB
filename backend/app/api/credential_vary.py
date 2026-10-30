"""Mark a response that depends on who asked as private and varying by credential.

A scientific detail read that carries deposit warnings differs by caller: the deposit's owner and curators get the
stored text, an authenticated reader gets a ``submission_ref``, an anonymous one gets neither. A shared cache that
stored one caller's answer would hand it to the next. A route that resolves the optional caller marks the request
(:func:`app.api.routes.scientific._actor.publish_read_actor`), and this middleware, on the way out, sets
``Cache-Control: private`` and adds the credential headers to ``Vary``.

There is no shared cache-header helper in the API today (the artifact download sets its own ``private, no-store``), so
this is written as the one place that does it for credential-dependent reads. Credentials are the ``X-API-Key`` header
and the ``tckdb_session`` cookie only; ``Authorization`` authenticates nothing here, so it is not in ``Vary``.

Pure ASGI, not ``BaseHTTPMiddleware``: it must not buffer or re-wrap a streamed body.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: ``request.state`` key a route sets when its answer depends on the caller.
STATE_KEY = "varies_by_credential"
VARY_HEADERS = ("Cookie", "X-API-Key")


class CredentialVaryMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start" and scope.get("state", {}).get(STATE_KEY):
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "private"
                present = {part.strip().lower() for part in headers.get("vary", "").split(",") if part.strip()}
                wanted = [h for h in VARY_HEADERS if h.lower() not in present]
                if wanted:
                    existing = headers.get("vary")
                    headers["Vary"] = ", ".join([existing, *wanted]) if existing else ", ".join(wanted)
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = ["STATE_KEY", "VARY_HEADERS", "CredentialVaryMiddleware"]
