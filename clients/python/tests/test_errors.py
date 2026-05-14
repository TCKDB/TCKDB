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
