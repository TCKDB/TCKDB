"""Size caps on one contribution bundle, shared by dry-run and submit (#586).

``/bundles/dry-run`` rehearses a full submit (#584), so an unbounded bundle
makes the server do an unbounded import's work -- holding row locks and a
pooled connection -- on every dry run. Two caps, both applied identically to
``/bundles/dry-run`` and ``/bundles/submit`` so a bundle one accepts the
other accepts:

* **Body size** (``settings.bundle_max_body_bytes``), refused with
  ``413 bundle_too_large`` *before the body is parsed*. This is a pure ASGI
  middleware rather than a route check because FastAPI validates the body
  before the route function runs: by then the whole JSON has been read and
  the model built. A ``Content-Length`` above the cap is refused without
  reading a byte. A chunked body, which declares no length, is counted as it
  streams and refused the moment it passes the cap, so at most the cap plus
  one chunk is ever held; the remainder is never read.
* **Record count** (``settings.bundle_max_records``, thermo plus kinetics
  uploads), refused with ``422 bundle_too_many_records``. This one can only
  be enforced after parsing: the count is a property of the parsed model, and
  the body cap above is what bounds the cost of getting that far.

Both settings' defaults, and the measurement behind them, are documented on
the settings themselves.
"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from tckdb_schemas.coded_error import CodedValidationError

from app.api.config import settings
from app.api.error_contract import error_envelope
from app.schemas.workflows.contribution_bundle import ContributionBundleV0

#: The routes that take a bundle. Matched on the exact path, POST only.
BUNDLE_BODY_PATHS = frozenset({
    "/api/v1/bundles/dry-run",
    "/api/v1/bundles/submit",
})


def bundle_record_count(bundle: ContributionBundleV0) -> int:
    """Records a bundle would import: thermo plus kinetics uploads."""
    return len(bundle.records.thermo_uploads) + len(bundle.records.kinetics_uploads)


def enforce_bundle_record_cap(bundle: ContributionBundleV0) -> int:
    """Refuse a bundle with more records than the configured cap.

    :returns: the record count, for callers that log it.
    :raises CodedValidationError: ``bundle_too_many_records``.
    """
    count = bundle_record_count(bundle)
    cap = settings.bundle_max_records
    if cap and count > cap:
        raise CodedValidationError(
            "bundle_too_many_records",
            f"A bundle may carry at most {cap:,} records; this one carries {count:,}. "
            "Split it into several bundles.",
            context={"max_records": cap, "records": count},
            message_prefix=False,
        )
    return count


def _too_large_response(limit: int, given: int | None) -> JSONResponse:
    context: dict[str, int] = {"max_bytes": limit}
    if given is not None:
        context["given_bytes"] = given
    return JSONResponse(
        status_code=413,
        content=error_envelope(
            f"A bundle may be at most {limit:,} bytes. Split it into several bundles.",
            code="bundle_too_large",
            context=context,
            fallback_code="bundle_too_large",
        ),
        headers={"Connection": "close"},
    )


class BundleBodyLimitMiddleware:
    """Refuse an oversized bundle request body before it is parsed."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = settings.bundle_max_body_bytes
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] not in BUNDLE_BODY_PATHS
            or not limit
        ):
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
                    # The app is still waiting on its body: tell it the client
                    # is gone, so it stops reading and parsing.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not refused:  # the app's reply to a request we already refused
                await send(message)

        await self.app(scope, counting_receive, guarded_send)


__all__ = [
    "BUNDLE_BODY_PATHS",
    "BundleBodyLimitMiddleware",
    "bundle_record_count",
    "enforce_bundle_record_cap",
]
