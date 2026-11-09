"""The frontend proxy's coded 413 (``request_body_too_large``).

``frontend/nginx.conf`` answers a body over its ``client_max_body_size`` with this envelope as JSON.
nginx cannot call Python, so the text is written in the config and ``tests/api/test_proxy_body_limit.py``
pins it to :func:`proxy_refusal_body`. The API never emits this code.
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
