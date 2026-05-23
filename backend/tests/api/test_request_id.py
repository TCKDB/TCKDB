"""Tests for the request-ID middleware.

Covers the three behaviors the deployment readiness audit (P1-4)
required:

- Generate an ``X-Request-ID`` when the caller does not send one.
- Echo a caller-provided ``X-Request-ID`` when it is safe.
- Reject (and replace) garbage / oversized incoming ids.
- The header is present on error responses too — so an operator can
  always correlate a 4xx/5xx back to the request log.
"""

from __future__ import annotations

import re

from app.api.request_id import REQUEST_ID_HEADER, resolve_request_id

_HEX_UUID = re.compile(r"^[0-9a-f]{32}$")


def test_request_id_generated_when_absent(client):
    """A fresh UUID-hex id is minted when the request has no header."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid is not None
    assert _HEX_UUID.match(rid), f"expected uuid hex, got {rid!r}"


def test_request_id_safe_incoming_is_echoed(client):
    """A safe incoming id is propagated unchanged onto the response."""
    given = "trace-abc.123_DEF"
    response = client.get(
        "/api/v1/health", headers={REQUEST_ID_HEADER: given}
    )
    assert response.status_code == 200
    assert response.headers.get(REQUEST_ID_HEADER) == given


def test_request_id_invalid_incoming_is_replaced(client):
    """Garbage in the header must not end up in the response."""
    # Contains a space (not in the safe pattern) and an angle bracket.
    bad = "<script>alert(1)</script> oops"
    response = client.get(
        "/api/v1/health", headers={REQUEST_ID_HEADER: bad}
    )
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid is not None
    assert rid != bad
    assert _HEX_UUID.match(rid)


def test_request_id_oversized_incoming_is_replaced(client):
    """Length-cap enforcement: 129+ char ids are dropped."""
    too_long = "a" * 200
    response = client.get(
        "/api/v1/health", headers={REQUEST_ID_HEADER: too_long}
    )
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid is not None
    assert rid != too_long
    assert _HEX_UUID.match(rid)


def test_request_id_present_on_error_responses(client):
    """Error envelopes carry the ID in the response header.

    Hitting a path that doesn't exist returns a Starlette 404; the
    middleware still wraps the response so the header is set.
    """
    response = client.get("/api/v1/this-route-does-not-exist")
    assert response.status_code in (404, 405)
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid is not None
    assert _HEX_UUID.match(rid)


def test_resolve_request_id_unit():
    """Pure-function checks of the validation rules.

    The middleware behavior depends on these so it's worth covering
    the cases without spinning up the app.
    """
    assert resolve_request_id(None) != ""
    assert resolve_request_id("") != ""
    assert resolve_request_id("safe.id_value-1") == "safe.id_value-1"
    # contains ``/`` which is not in the allow-list -> replaced
    assert resolve_request_id("not/safe") != "not/safe"
    # exact length cap (128) is allowed
    ok = "a" * 128
    assert resolve_request_id(ok) == ok
    # 129 is not allowed
    assert resolve_request_id("a" * 129) != "a" * 129
