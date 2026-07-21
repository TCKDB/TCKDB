"""Typed scientific responses and safe pagination helpers."""

from __future__ import annotations

import inspect
import json
from typing import Any, get_args, get_type_hints
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import tckdb_client
from conftest import make_client
from tckdb_client import (
    ArtifactSearchResponse,
    KineticsSearchResponse,
    NetworkKineticsSearchResponse,
    NetworkSearchResponse,
    ReactionSearchResponse,
    SpeciesCalculationsSearchResponse,
    SpeciesSearchResponse,
    StatmechSearchResponse,
    TCKDBClient,
    TCKDBPaginationError,
    ThermoSearchResponse,
    TransportSearchResponse,
)
from tckdb_client.pagination import iter_paginated_records


def _page(*, offset: int = 0, limit: int = 50, total: int = 0) -> dict[str, Any]:
    returned = min(limit, max(total - offset, 0))
    return {
        "request": {"filter": {}, "sort": "stable", "collapse": "all", "include": []},
        "review_summary": {"total": total},
        "records": [{"ordinal": offset + index} for index in range(returned)],
        "pagination": {
            "offset": offset,
            "limit": limit,
            "returned": returned,
            "total": total,
        },
    }


@pytest.mark.parametrize(
    ("method_name", "response_name", "response_type"),
    [
        ("search_species", "SpeciesSearchResponse", SpeciesSearchResponse),
        ("search_reactions", "ReactionSearchResponse", ReactionSearchResponse),
        ("search_thermo", "ThermoSearchResponse", ThermoSearchResponse),
        ("search_kinetics", "KineticsSearchResponse", KineticsSearchResponse),
        (
            "search_species_calculations",
            "SpeciesCalculationsSearchResponse",
            SpeciesCalculationsSearchResponse,
        ),
        ("search_networks", "NetworkSearchResponse", NetworkSearchResponse),
        (
            "search_network_kinetics",
            "NetworkKineticsSearchResponse",
            NetworkKineticsSearchResponse,
        ),
        ("search_statmech", "StatmechSearchResponse", StatmechSearchResponse),
        ("search_transport", "TransportSearchResponse", TransportSearchResponse),
        ("search_artifacts", "ArtifactSearchResponse", ArtifactSearchResponse),
    ],
)
def test_search_methods_publish_typed_dict_annotations(
    method_name: str,
    response_name: str,
    response_type: object,
) -> None:
    hints = get_type_hints(getattr(TCKDBClient, method_name))

    assert hints["return"] == response_type
    assert response_name in tckdb_client.__all__
    assert getattr(tckdb_client, response_name) == response_type


@pytest.mark.parametrize(
    ("method_name", "path", "kwargs"),
    [
        (
            "search_networks",
            "/api/v1/scientific/networks/search",
            {"network_ref": "net_a"},
        ),
        (
            "search_network_kinetics",
            "/api/v1/scientific/network-kinetics/search",
            {"source_species_entry_refs": ["spe_a", "spe_a"]},
        ),
        (
            "search_statmech",
            "/api/v1/scientific/statmech/search",
            {"statmech_ref": "sm_a"},
        ),
        (
            "search_transport",
            "/api/v1/scientific/transport/search",
            {"transport_ref": "tr_a"},
        ),
        (
            "search_artifacts",
            "/api/v1/scientific/artifacts/search",
            {"artifact_kind": "log"},
        ),
    ],
)
def test_new_search_methods_default_to_post_and_return_raw_dicts(
    method_name: str,
    path: str,
    kwargs: dict[str, Any],
) -> None:
    body = _page(total=0)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client, recorder = make_client(handler)
    result = getattr(client, method_name)(include=["review"], **kwargs)

    assert result == body
    assert isinstance(result, dict)
    assert recorder.last.method == "POST"
    assert urlsplit(recorder.last.url).path == path
    payload = recorder.last.json()
    assert payload["include"] == ["review"]
    for key, value in kwargs.items():
        assert payload[key] == value


def test_network_kinetics_get_preserves_repeated_multiset_filters() -> None:
    client, recorder = make_client(
        lambda _request: httpx.Response(200, json=_page(total=0))
    )

    client.search_network_kinetics(
        source_species_entry_refs=["spe_a", "spe_a", "spe_b"],
        include=["coefficients", "points"],
        method_http="GET",
    )

    query = parse_qs(urlsplit(recorder.last.url).query)
    assert recorder.last.method == "GET"
    assert query["source_species_entry_refs"] == ["spe_a", "spe_a", "spe_b"]
    assert query["include"] == ["coefficients", "points"]


@pytest.mark.parametrize(
    "method_name",
    [
        "search_networks",
        "search_network_kinetics",
        "search_statmech",
        "search_transport",
        "search_artifacts",
    ],
)
def test_new_search_http_methods_publish_a_closed_literal(method_name: str) -> None:
    hints = get_type_hints(getattr(TCKDBClient, method_name))

    assert get_args(hints["method_http"]) == ("GET", "POST")


@pytest.mark.parametrize(
    "method_name",
    [
        "search_networks",
        "search_network_kinetics",
        "search_statmech",
        "search_transport",
        "search_artifacts",
    ],
)
def test_new_search_methods_reject_an_invalid_http_method(method_name: str) -> None:
    client, recorder = make_client(
        lambda _request: httpx.Response(200, json=_page(total=0))
    )

    with pytest.raises(ValueError, match="method_http must be 'GET' or 'POST'"):
        getattr(client, method_name)(method_http="PATCH")

    assert recorder.requests == []


def test_new_search_methods_keep_case_insensitive_runtime_compatibility() -> None:
    client, recorder = make_client(
        lambda _request: httpx.Response(200, json=_page(total=0))
    )

    client.search_networks(method_http="get")  # type: ignore[arg-type]

    assert recorder.last.method == "GET"


@pytest.mark.parametrize(
    "iterator_name",
    [
        "iter_species",
        "iter_reactions",
        "iter_thermo",
        "iter_kinetics",
        "iter_species_calculations",
        "iter_networks",
        "iter_network_kinetics",
        "iter_statmech",
        "iter_transport",
        "iter_artifacts",
    ],
)
def test_every_supported_search_has_a_lazy_complete_iterator(
    iterator_name: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            query = parse_qs(urlsplit(str(request.url)).query)
            offset = int(query["offset"][0])
            limit = int(query["limit"][0])
        else:
            payload = json.loads(request.content)
            offset = payload["offset"]
            limit = payload["limit"]
        return httpx.Response(200, json=_page(offset=offset, limit=limit, total=3))

    client, _recorder = make_client(handler)
    iterator = getattr(client, iterator_name)(include=["review"], limit=2)

    assert inspect.isgenerator(iterator)
    assert [record["ordinal"] for record in iterator] == [0, 1, 2]
    assert len(requests) == 2


def test_iterator_keeps_filters_and_includes_stable_while_advancing_offset() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        bodies.append(payload)
        return httpx.Response(
            200,
            json=_page(offset=payload["offset"], limit=payload["limit"], total=3),
        )

    client, _recorder = make_client(handler)
    records = list(
        client.iter_network_kinetics(
            source_species_entry_refs=["spe_a", "spe_a"],
            include=["coefficients", "review"],
            limit=2,
        )
    )

    assert len(records) == 3
    assert [body["offset"] for body in bodies] == [0, 2]
    assert all(body["limit"] == 2 for body in bodies)
    assert all(
        body["source_species_entry_refs"] == ["spe_a", "spe_a"] for body in bodies
    )
    assert all(body["include"] == ["coefficients", "review"] for body in bodies)


@pytest.mark.parametrize(
    ("page", "message"),
    [
        ({"records": []}, "pagination"),
        (
            {
                "records": [{}],
                "pagination": {"offset": 0, "limit": 1, "returned": 0, "total": 1},
            },
            r"len\(records\)",
        ),
        (
            {
                "records": [{}],
                "pagination": {"offset": 4, "limit": 1, "returned": 1, "total": 5},
            },
            "requested offset",
        ),
        (
            {
                "records": [],
                "pagination": {"offset": 0, "limit": 1, "returned": 0, "total": 1},
            },
            "did not advance",
        ),
    ],
)
def test_iterator_rejects_malformed_or_nonadvancing_pages(
    page: dict[str, Any],
    message: str,
) -> None:
    def fetch(**_kwargs: Any) -> Any:
        return page

    with pytest.raises(TCKDBPaginationError, match=message):
        list(iter_paginated_records(fetch, {"limit": 1}))


def test_iterator_rejects_a_total_that_changes_between_pages() -> None:
    pages = iter([_page(limit=1, total=2), _page(offset=1, limit=1, total=3)])

    with pytest.raises(TCKDBPaginationError, match="total changed"):
        list(iter_paginated_records(lambda **_kwargs: next(pages), {"limit": 1}))


def test_iterator_rejects_a_server_offset_that_does_not_advance() -> None:
    pages = iter([_page(limit=1, total=2), _page(limit=1, total=2)])

    with pytest.raises(TCKDBPaginationError, match="requested offset"):
        list(iter_paginated_records(lambda **_kwargs: next(pages), {"limit": 1}))


def test_collapsed_iterator_stops_after_the_single_collapsed_record() -> None:
    calls = 0

    def fetch(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        page = _page(limit=1, total=8)
        page["request"]["collapse"] = "first"
        return page

    records = list(iter_paginated_records(fetch, {"collapse": "first", "limit": 1}))

    assert records == [{"ordinal": 0}]
    assert calls == 1


def test_collapsed_iterator_accepts_empty_page_after_offset() -> None:
    calls = 0

    def fetch(**parameters: Any) -> Any:
        nonlocal calls
        calls += 1
        return {
            "records": [],
            "pagination": {
                "offset": parameters["offset"],
                "limit": parameters["limit"],
                "returned": 0,
                "total": 8,
            },
        }

    records = list(
        iter_paginated_records(
            fetch,
            {"collapse": "first", "offset": 1, "limit": 1},
        )
    )

    assert records == []
    assert calls == 1
