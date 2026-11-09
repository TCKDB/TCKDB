"""The frontend proxy's coded 413 (``request_body_too_large``).

nginx (``frontend/nginx.conf``) caps request bodies before the API sees them:
``100m`` on the upload, job and calculation-artifact routes and ``10m`` on every
other ``/api/`` route. Over the cap, nginx used to answer with its own bare HTML
413, which no client could tell from any other 413. ``nginx.conf`` now answers
with the API's error envelope instead, written by hand in the config because
nginx cannot call Python.

This module is the single description of that body. ``nginx.conf`` carries the
literal text, and ``tests/api/test_proxy_body_limit.py`` fails if the text and
:func:`proxy_refusal_body` ever disagree, so the proxy's answer cannot drift from
the envelope the catalogue documents. The API never emits this code itself; a
body over these caps does not reach it.

The edge in front of nginx (Cloudflare, 100,000,000 bytes) answers its own HTML
413 before the request reaches nginx and cannot be customised on the plans in
use; ``tckdb-client`` explains that one.
"""

from __future__ import annotations

from app.api.error_contract import error_envelope

CODE_REQUEST_BODY_TOO_LARGE = "request_body_too_large"

#: ``client_max_body_size 100m`` (upload, job and artifact routes), in bytes.
UPLOAD_ROUTE_MAX_BYTES = 100 * 1024 * 1024

#: ``client_max_body_size 10m`` (every other ``/api/`` route), in bytes.
DEFAULT_ROUTE_MAX_BYTES = 10 * 1024 * 1024


def proxy_refusal_body(max_bytes: int) -> dict[str, object]:
    """The JSON body nginx returns for a 413 on a route capped at ``max_bytes``."""
    return error_envelope(
        f"The request body is larger than the {max_bytes:,} bytes the server accepts on this route. "
        "Send a smaller body.",
        code=CODE_REQUEST_BODY_TOO_LARGE,
        context={"max_bytes": max_bytes},
        fallback_code=CODE_REQUEST_BODY_TOO_LARGE,
    )
