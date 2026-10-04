"""Body size cap on the selected-network-export route, enforced before the body is parsed.

``POST /scientific/networks/{ref}/kinetics/export-selected`` takes a whole decision manifest, which the server then
replays and re-derives. FastAPI validates a request body before the route function runs, so a route-level check would
come after the whole JSON had been read and the model built. This is a pure ASGI middleware, like
:mod:`app.api.bundle_limits`: a ``Content-Length`` above the cap is refused (``413 network_export_body_too_large``)
without reading a byte, and a chunked body is counted as it streams and refused the moment it passes the cap.

The cap is the selection snapshot bound (the largest manifest the server ever produces) plus a fixed allowance for
the rest of the request (refs and options); a document larger than that cannot be one of the server's manifests.
"""

from __future__ import annotations

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.error_contract import error_envelope
from app.services.network_selection.models import BOUNDS_V1

CODE_BODY_TOO_LARGE = "network_export_body_too_large"
#: Room for ``representation_refs`` (at most 10000 refs) and the options around the manifest.
REQUEST_ALLOWANCE_BYTES = 1024 * 1024
MAX_BODY_BYTES = BOUNDS_V1.snapshot_bytes + REQUEST_ALLOWANCE_BYTES
_PATH = re.compile(r"^/api/v1/scientific/networks/[^/]+/kinetics/export-selected$")


def _too_large_response(limit: int, given: int | None) -> JSONResponse:
    context: dict[str, int] = {"max_bytes": limit}
    if given is not None:
        context["given_bytes"] = given
    return JSONResponse(
        status_code=413,
        content=error_envelope(
            f"An export request may be at most {limit:,} bytes: that is the largest selection manifest the server "
            "produces plus the choice.",
            code=CODE_BODY_TOO_LARGE,
            context=context,
            fallback_code=CODE_BODY_TOO_LARGE,
        ),
        headers={"Connection": "close"},
    )


class NetworkExportBodyLimitMiddleware:
    """Refuse an oversized export request body before it is parsed."""

    def __init__(self, app: ASGIApp, *, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self.limit
        if scope["type"] != "http" or scope["method"] != "POST" or not _PATH.match(scope["path"]):
            await self.app(scope, receive, send)
            return

        declared: int | None = None
        for name, value in scope["headers"]:
            if name == b"content-length":
                declared = int(value) if value.isdigit() else None
                break

        async def refuse(given: int | None) -> None:
            await _too_large_response(limit, given)(scope, receive, send)

        if declared is not None and declared > limit:
            await refuse(declared)
            return

        seen = 0
        refused = False

        async def counting_receive() -> Message:
            nonlocal seen, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    refused = True
                    await refuse(None)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not refused:
                await send(message)

        await self.app(scope, counting_receive, guarded_send)


__all__ = ["CODE_BODY_TOO_LARGE", "MAX_BODY_BYTES", "NetworkExportBodyLimitMiddleware"]
