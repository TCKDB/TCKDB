"""A 2xx body that is not the API's answer is refused, not returned (#568).

Point ``base_url`` at a site root (``https://host``) instead of the API root
(``https://host/api/v1``) and the web app's single-page fallback answers
every path with its ``index.html`` and a 200. The client used to hand that
HTML back as ``data``; callers then indexed a string and died with a bare
``TypeError`` far from the cause. These tests pin the refusal, its message,
and the paths it must not touch.
"""

from __future__ import annotations

import httpx
import pytest

from tckdb_client import (
    TCKDBClient,
    TCKDBHTTPError,
    TCKDBUnexpectedResponseError,
)

SITE_ROOT = "https://tckdb.example"
API_ROOT = SITE_ROOT + "/api/v1"
SPA_HTML = (
    b"<!doctype html><html><head><title>TCKDB</title></head>"
    b"<body><div id=root></div></body></html>"
)


def _spa(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=SPA_HTML,
        headers={"content-type": "text/html; charset=utf-8"},
    )


def _client(handler, base_url: str = SITE_ROOT) -> TCKDBClient:
    return TCKDBClient(
        base_url,
        api_key="tck_test_key_value_1234",
        transport=httpx.MockTransport(handler),
    )


def test_html_200_on_a_json_read_is_refused_with_the_likely_cause():
    client = _client(_spa)

    with pytest.raises(TCKDBUnexpectedResponseError) as info:
        client.get_calculation("calc_abc", include=["results"])

    exc = info.value
    message = str(exc)
    requested = f"{SITE_ROOT}/scientific/calculations/calc_abc?include=results"
    assert exc.url == requested
    assert f"GET {requested} returned HTTP 200" in message
    assert "HTML page" in message and "not JSON" in message
    # The fix the caller needs, spelled out with their own host.
    assert f"e.g. {API_ROOT} " in message
    assert exc.status_code == 200
    assert exc.content_type == "text/html; charset=utf-8"
    assert exc.response_text == SPA_HTML.decode()
    # Reserved for codes the server sent; no TCKDB server sent this.
    assert exc.code is None
    # Existing ``except TCKDBHTTPError`` handlers keep catching it.
    assert isinstance(exc, TCKDBHTTPError)


def test_html_200_on_a_write_is_refused_before_the_caller_reads_it():
    """The upload path goes through the same funnel as reads."""

    client = _client(_spa)

    with pytest.raises(TCKDBUnexpectedResponseError) as info:
        client.request_json("POST", "/uploads/conformers", json={"x": 1})

    assert str(info.value).startswith(
        f"POST {SITE_ROOT}/uploads/conformers returned HTTP 200"
    )


def test_html_without_a_content_type_is_still_recognised_as_html():
    client = _client(lambda _r: httpx.Response(200, content=SPA_HTML))

    with pytest.raises(TCKDBUnexpectedResponseError) as info:
        client.get_json("/auth/me")

    assert "HTML page (no content-type)" in str(info.value)
    assert f"e.g. {API_ROOT} " in str(info.value)


def test_html_behind_an_api_root_base_url_does_not_suggest_appending_again():
    client = _client(_spa, base_url=API_ROOT)

    with pytest.raises(TCKDBUnexpectedResponseError) as info:
        client.health()

    message = str(info.value)
    assert "/api/v1/api/v1" not in message
    assert "already ends in /api/v1" in message


def test_a_non_html_non_json_body_is_refused_as_not_json():
    client = _client(
        lambda _r: httpx.Response(
            200, content=b"ok", headers={"content-type": "text/plain"}
        )
    )

    with pytest.raises(TCKDBUnexpectedResponseError) as info:
        client.health()

    assert "a body that is not JSON (text/plain)" in str(info.value)


# ---------------------------------------------------------------------------
# Controls: what must keep working.
# ---------------------------------------------------------------------------


def test_control_a_json_body_is_returned_unchanged():
    client = _client(lambda _r: httpx.Response(200, json={"record": {"id": 1}}))

    assert client.get_calculation("calc_abc") == {"record": {"id": 1}}


def test_control_an_empty_success_body_is_still_none():
    client = _client(lambda _r: httpx.Response(204))

    response = client.request_json("DELETE", "/something")

    assert response.data is None
    assert response.status_code == 204


def test_control_a_non_success_html_body_keeps_its_http_error():
    """Error responses were already raised; their type must not change."""

    client = _client(
        lambda _r: httpx.Response(
            502, content=b"<html>Bad Gateway</html>",
            headers={"content-type": "text/html"},
        )
    )

    with pytest.raises(TCKDBHTTPError) as info:
        client.health()

    assert type(info.value) is TCKDBHTTPError
    assert info.value.status_code == 502


# ---------------------------------------------------------------------------
# Raw (non-JSON) endpoints.
# ---------------------------------------------------------------------------


def test_ndjson_export_refuses_an_html_page():
    client = _client(_spa)

    with pytest.raises(TCKDBUnexpectedResponseError) as info:
        list(client.export_ndjson())

    assert f"e.g. {API_ROOT} " in str(info.value)


def test_chemkin_export_refuses_an_html_page():
    client = _client(_spa)

    with pytest.raises(TCKDBUnexpectedResponseError):
        client.export_chemkin({"all_reactions": True})


def test_artifact_download_still_returns_an_html_artifact_verbatim():
    """The server labels downloads by stored filename, so an uploaded
    ``.html`` file is a legitimate ``text/html`` 200 -- not refused here.
    (The CLI's digest check is what catches a site root on this path.)"""

    client = _client(_spa, base_url=API_ROOT)

    assert client.download_artifact("a" * 64) == SPA_HTML
