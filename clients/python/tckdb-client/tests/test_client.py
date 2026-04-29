"""Tests for client wiring: URL handling, headers, and helpers."""

from __future__ import annotations

import compileall
import pathlib

import httpx
import pytest

from tckdb_client import TCKDBClient, TCKDBResponse, UPLOAD_ENDPOINTS
from tckdb_client.client import (
    API_KEY_HEADER,
    IDEMPOTENCY_HEADER,
    IDEMPOTENCY_REPLAYED_HEADER,
)
from tckdb_client.errors import TCKDBAuthenticationError
from conftest import BASE_URL, make_client


def _ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=body or {"ok": True})


# -------------------------------------------------------------------- 1
def test_base_url_normalization_strips_trailing_slash() -> None:
    client = TCKDBClient("http://example.com/api/v1/")
    assert client.base_url == "http://example.com/api/v1"
    client2 = TCKDBClient("http://example.com/api/v1//")
    assert client2.base_url == "http://example.com/api/v1"


# -------------------------------------------------------------------- 2
@pytest.mark.parametrize(
    "path,expected",
    [
        ("/auth/me", f"{BASE_URL}/auth/me"),
        ("auth/me", f"{BASE_URL}/auth/me"),
        ("/uploads/thermo", f"{BASE_URL}/uploads/thermo"),
    ],
)
def test_path_joining_handles_leading_slash(path: str, expected: str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _ok()

    client, _ = make_client(handler)
    client.get_json(path)
    assert seen == [expected]


def test_path_joining_does_not_duplicate_api_v1() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _ok()

    client, _ = make_client(handler)
    client.get_json("/auth/me")
    assert "/api/v1/api/v1" not in seen[0]


# -------------------------------------------------------------------- 3
def test_authenticated_request_sends_api_key_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler, api_key="tck_test_key_value_xyz")
    client.me()
    assert recorder.last.headers.get(API_KEY_HEADER.lower()) == "tck_test_key_value_xyz"


# -------------------------------------------------------------------- 4
def test_idempotency_key_header_sent_when_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler)
    client.post_json(
        "/uploads/thermo",
        {"a": 1},
        idempotency_key="abcdefghij1234567890",
    )
    assert recorder.last.headers.get(IDEMPOTENCY_HEADER.lower()) == "abcdefghij1234567890"


def test_idempotency_key_omitted_when_not_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler)
    client.post_json("/uploads/thermo", {"a": 1})
    assert IDEMPOTENCY_HEADER.lower() not in recorder.last.headers


def test_idempotency_key_validated_before_send() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler)
    with pytest.raises(ValueError):
        client.post_json("/x", {}, idempotency_key="too-short")
    assert recorder.requests == []  # never sent


# -------------------------------------------------------------------- 5
def test_health_works_without_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    client, recorder = make_client(handler, api_key=None)
    body = client.health()
    assert body == {"status": "ok"}
    assert API_KEY_HEADER.lower() not in recorder.last.headers


# -------------------------------------------------------------------- 6
def test_me_without_api_key_raises_client_side() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()  # never hit

    client, recorder = make_client(handler, api_key=None)
    with pytest.raises(TCKDBAuthenticationError):
        client.me()
    assert recorder.requests == []


# -------------------------------------------------------------------- 7
def test_post_json_returns_decoded_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"received": True, "n": 7})

    client, _ = make_client(handler)
    result = client.post_json("/uploads/thermo", {"a": 1})
    assert result == {"received": True, "n": 7}


# -------------------------------------------------------------------- 8
def test_request_json_wrapper_exposes_status_headers_and_replay() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True},
            headers={IDEMPOTENCY_REPLAYED_HEADER: "true"},
        )

    client, _ = make_client(handler)
    response = client.request_json(
        "POST",
        "/uploads/thermo",
        json={"a": 1},
        idempotency_key="abcdefghij1234567890",
    )
    assert isinstance(response, TCKDBResponse)
    assert response.status_code == 200
    assert response.data == {"ok": True}
    assert response.idempotency_replayed is True


def test_replay_flag_false_when_header_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"ok": True})

    client, _ = make_client(handler)
    response = client.request_json("POST", "/uploads/thermo", json={"a": 1})
    assert response.idempotency_replayed is False


# -------------------------------------------------------------------- 16
def test_bundle_dry_run_posts_to_dry_run_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"bundle_valid": True})

    client, recorder = make_client(handler)
    result = client.bundle_dry_run({"records": []})
    assert result == {"bundle_valid": True}
    assert recorder.last.method == "POST"
    assert recorder.last.url.endswith("/bundles/dry-run")
    # dry-run never carries idempotency by default
    assert IDEMPOTENCY_HEADER.lower() not in recorder.last.headers


# -------------------------------------------------------------------- 17
def test_bundle_submit_posts_to_submit_endpoint_with_idempotency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"submission_id": 42})

    client, recorder = make_client(handler)
    result = client.bundle_submit(
        {"records": []}, idempotency_key="abcdefghij1234567890"
    )
    assert result == {"submission_id": 42}
    assert recorder.last.url.endswith("/bundles/submit")
    assert recorder.last.headers.get(IDEMPOTENCY_HEADER.lower()) == "abcdefghij1234567890"


# -------------------------------------------------------------------- 18
def test_upload_helper_resolves_short_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler)
    client.upload("thermo", {"x": 1})
    assert recorder.last.url.endswith(UPLOAD_ENDPOINTS["thermo"])


def test_upload_helper_accepts_explicit_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler)
    client.upload("/uploads/conformers", {"x": 1})
    assert recorder.last.url.endswith("/uploads/conformers")


def test_upload_helper_rejects_unknown_short_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler)
    with pytest.raises(ValueError):
        client.upload("not-a-real-endpoint", {"x": 1})
    assert recorder.requests == []


# -------------------------------------------------------------------- 20
def test_examples_compile() -> None:
    examples_dir = pathlib.Path(__file__).resolve().parents[1] / "examples"
    assert examples_dir.is_dir(), examples_dir
    ok = compileall.compile_dir(
        str(examples_dir), quiet=1, force=True
    )
    assert ok == 1


# -------------------------------------------------------------------- misc
def test_json_content_type_set_on_post() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler)
    client.post_json("/uploads/thermo", {"a": 1})
    assert recorder.last.headers.get("content-type") == "application/json"


def test_get_does_not_set_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler)
    client.me()
    assert recorder.last.headers.get("content-type") is None


def test_context_manager_closes_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    with make_client(handler)[0] as client:
        client.health()
