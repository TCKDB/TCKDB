"""Tests for HTTP status to structured exception mapping."""

from __future__ import annotations

import httpx
import pytest

from tckdb_client import (
    TCKDBAuthenticationError,
    TCKDBConflictError,
    TCKDBConnectionError,
    TCKDBForbiddenError,
    TCKDBHTTPError,
    TCKDBIdempotencyConflictError,
    TCKDBValidationError,
)
from conftest import make_client


def _json_response(status_code: int, body: dict) -> httpx.Response:
    return httpx.Response(status_code=status_code, json=body)


def test_401_maps_to_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(401, {"detail": "Invalid API key."})

    client, _ = make_client(handler)
    with pytest.raises(TCKDBAuthenticationError) as info:
        client.me()
    assert info.value.status_code == 401
    assert info.value.detail == "Invalid API key."


def test_403_maps_to_forbidden_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(403, {"detail": "Forbidden."})

    client, _ = make_client(handler)
    with pytest.raises(TCKDBForbiddenError) as info:
        client.post_json("/some/path", {"a": 1})
    assert info.value.status_code == 403


def test_422_maps_to_validation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            422, {"detail": [{"loc": ["body", "x"], "msg": "missing"}]}
        )

    client, _ = make_client(handler)
    with pytest.raises(TCKDBValidationError) as info:
        client.post_json("/uploads/thermo", {})
    assert info.value.status_code == 422
    assert isinstance(info.value.detail, list)


def test_409_idempotency_conflict_maps_to_subtype() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            409,
            {
                "detail": "Idempotency key reused with a different payload.",
                "code": "idempotency_conflict",
                "endpoint": "/bundles/submit",
            },
        )

    client, _ = make_client(handler)
    with pytest.raises(TCKDBIdempotencyConflictError) as info:
        client.bundle_submit({"a": 1}, idempotency_key="abcdefghij1234567890")
    assert info.value.code == "idempotency_conflict"
    assert info.value.status_code == 409
    # subclass of generic conflict by design
    assert isinstance(info.value, TCKDBConflictError)


def test_409_other_maps_to_generic_conflict_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            409,
            {
                "detail": "Resource conflicts with an existing record.",
                "code": "unique_conflict",
            },
        )

    client, _ = make_client(handler)
    with pytest.raises(TCKDBConflictError) as info:
        client.post_json("/some/path", {"a": 1})
    assert not isinstance(info.value, TCKDBIdempotencyConflictError)
    assert info.value.code == "unique_conflict"


def test_500_maps_to_generic_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(500, {"detail": "boom"})

    client, _ = make_client(handler)
    with pytest.raises(TCKDBHTTPError) as info:
        client.get_json("/health")
    assert info.value.status_code == 500
    assert not isinstance(info.value, TCKDBValidationError)


def test_timeout_maps_to_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout", request=request)

    client, _ = make_client(handler)
    with pytest.raises(TCKDBConnectionError):
        client.health()


def test_network_error_maps_to_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, _ = make_client(handler)
    with pytest.raises(TCKDBConnectionError):
        client.health()


def test_error_carries_response_text_for_non_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=502, text="Bad Gateway")

    client, _ = make_client(handler)
    with pytest.raises(TCKDBHTTPError) as info:
        client.health()
    assert info.value.status_code == 502
    assert info.value.response_text == "Bad Gateway"
    assert info.value.response_json is None


# --- 413: say what happened when no TCKDB code arrives --------------------

_EDGE_HTML = "<html><body><h1>413 Request Entity Too Large</h1><p>edge page</p></body></html>"


def _post_413(response: httpx.Response) -> TCKDBHTTPError:
    client, _ = make_client(lambda request: response)
    with pytest.raises(TCKDBHTTPError) as info:
        client.post_json("/uploads/computed-species", {"a": 1})
    return info.value


def test_html_413_explains_the_size_limit() -> None:
    error = _post_413(httpx.Response(413, text=_EDGE_HTML, headers={"content-type": "text/html"}))
    assert error.status_code == 413
    assert error.code is None
    message = str(error)
    assert "too large" in message and "proxy" in message
    assert "did not state its limit" in message
    assert "100,000,000 bytes" in message and "71.5 MiB" in message
    assert "upload_artifacts" in message and "inline_artifacts" not in message
    assert "hosted" not in message
    assert "edge page" not in message  # nothing from the body
    assert error.response_text == _EDGE_HTML  # the raw page is still on the exception


def test_empty_413_explains_the_size_limit() -> None:
    error = _post_413(httpx.Response(413))
    assert error.code is None
    assert "too large" in str(error) and "100,000,000 bytes" in str(error)


@pytest.mark.parametrize(
    "code",
    ["request_body_too_large", "anonymous_body_too_large", "bundle_too_large"],
)
def test_coded_413_keeps_its_code_and_message(code: str) -> None:
    error = _post_413(
        _json_response(
            413,
            {"code": code, "detail": f"server words for {code}", "context": {"max_bytes": 5}},
        )
    )
    assert error.code == code
    assert str(error) == f"server words for {code}"
