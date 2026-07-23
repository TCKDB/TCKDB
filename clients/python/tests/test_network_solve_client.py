"""Focused network-solve scientific-read client contract tests."""

from __future__ import annotations

import inspect
import json
from typing import Any, get_args, get_type_hints
from urllib.parse import parse_qs, urlsplit

import httpx

import tckdb_client
from conftest import make_client
from tckdb_client import NetworkSolveRecord, NetworkSolveSearchResponse, TCKDBClient


def _page(*, offset: int = 0, limit: int = 50, total: int = 0) -> dict[str, Any]:
    returned = min(limit, max(total - offset, 0))
    return {
        "request": {"filter": {}, "sort": "stable", "include": []},
        "review_summary": {"total": total},
        "records": [{"ordinal": offset + index} for index in range(returned)],
        "pagination": {
            "offset": offset,
            "limit": limit,
            "returned": returned,
            "total": total,
        },
    }


def test_network_solve_search_types_are_exported() -> None:
    hints = get_type_hints(TCKDBClient.search_network_solves)

    assert hints["return"] == NetworkSolveSearchResponse
    assert get_args(hints["method_http"]) == ("GET", "POST")
    assert {
        "network_solve",
        "network",
        "evidence_summary",
        "available_sections",
    } <= set(NetworkSolveRecord.__annotations__)
    assert "NetworkSolveRecord" in tckdb_client.__all__
    assert "NetworkSolveSearchResponse" in tckdb_client.__all__
    assert tckdb_client.NetworkSolveRecord is NetworkSolveRecord
    assert tckdb_client.NetworkSolveSearchResponse is NetworkSolveSearchResponse


def test_search_network_solves_posts_the_backend_search_contract() -> None:
    client, recorder = make_client(
        lambda _request: httpx.Response(200, json=_page(total=0))
    )

    result = client.search_network_solves(
        network_solve_ref="nsolve_a",
        has_kinetics=True,
        include=["kinetics", "review"],
    )

    assert result["records"] == []
    assert recorder.last.method == "POST"
    assert (
        urlsplit(recorder.last.url).path == "/api/v1/scientific/network-solves/search"
    )
    assert recorder.last.json() == {
        "network_solve_ref": "nsolve_a",
        "has_kinetics": True,
        "include": ["kinetics", "review"],
    }


def test_search_network_solves_get_preserves_include_tokens() -> None:
    client, recorder = make_client(
        lambda _request: httpx.Response(200, json=_page(total=0))
    )

    client.search_network_solves(
        network_ref="net_a",
        include=["bath_gas", "kinetics"],
        method_http="GET",
    )

    query = parse_qs(urlsplit(recorder.last.url).query)
    assert recorder.last.method == "GET"
    assert query["network_ref"] == ["net_a"]
    assert query["include"] == ["bath_gas", "kinetics"]


def test_iter_network_solves_paginates_with_stable_filters() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        body = bodies[-1]
        return httpx.Response(
            200,
            json=_page(offset=body["offset"], limit=body["limit"], total=3),
        )

    client, _recorder = make_client(handler)
    iterator = client.iter_network_solves(
        network_ref="net_a", include=["kinetics", "review"], limit=2
    )

    assert inspect.isgenerator(iterator)
    assert [record["ordinal"] for record in iterator] == [0, 1, 2]
    assert [body["offset"] for body in bodies] == [0, 2]
    assert all(body["network_ref"] == "net_a" for body in bodies)
    assert all(body["include"] == ["kinetics", "review"] for body in bodies)


def test_get_network_solve_accepts_ref_or_id_and_repeats_include_tokens() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(urlsplit(str(request.url)).path)
        return httpx.Response(200, json={"record": {"network_solve": {}}})

    client, recorder = make_client(handler)

    by_ref = client.get_network_solve("nsolve_a", include=["bath_gas", "review"])
    by_id = client.get_network_solve(42)

    assert by_ref == {"record": {"network_solve": {}}}
    assert by_id == {"record": {"network_solve": {}}}
    assert paths == [
        "/api/v1/scientific/network-solves/nsolve_a",
        "/api/v1/scientific/network-solves/42",
    ]
    first_query = parse_qs(urlsplit(recorder.requests[0].url).query)
    assert recorder.requests[0].method == "GET"
    assert first_query["include"] == ["bath_gas", "review"]
