"""Python-client compatibility tests for machine-readable API errors."""

from __future__ import annotations

import httpx
import pytest

from conftest import make_client
from tckdb_client.errors import TCKDBHTTPError


def test_structured_error_code_is_preserved() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "code": "unsupported_filter",
                "detail": "unsupported_filter: filter is unavailable",
                "context": {"filters": ["inchi"]},
            },
        )

    client, _ = make_client(handler)
    with pytest.raises(TCKDBHTTPError) as error:
        client.get_json("/scientific/species/search")

    assert error.value.code == "unsupported_filter"
    assert error.value.response_json["context"] == {"filters": ["inchi"]}


def test_legacy_detail_prefix_is_used_when_top_level_code_is_absent() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": "unknown_include_token: token is unavailable"},
        )

    client, _ = make_client(handler)
    with pytest.raises(TCKDBHTTPError) as error:
        client.get_json("/scientific/species/search")

    assert error.value.code == "unknown_include_token"
