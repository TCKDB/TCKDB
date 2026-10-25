"""Every list method that takes ``offset`` takes ``cursor``, sends it, and the iterators follow ``next_cursor``."""

from __future__ import annotations

import inspect
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import make_client

from tckdb_client import TCKDBClient

#: Offset-taking methods that are not cursor-paged: the two NDJSON exports have no envelope to carry a cursor.
NOT_CURSOR_PAGED = {"export_ml_species", "export_ml_reactions"}


def _offset_methods() -> list[str]:
    return sorted(
        name
        for name, member in inspect.getmembers(TCKDBClient, inspect.isfunction)
        if not name.startswith("_") and "offset" in inspect.signature(member).parameters
    )


def test_every_method_that_takes_offset_takes_cursor_unless_it_streams():
    methods = _offset_methods()
    assert len(methods) > 25
    missing = [
        name
        for name in methods
        if name not in NOT_CURSOR_PAGED and "cursor" not in inspect.signature(getattr(TCKDBClient, name)).parameters
    ]
    assert missing == []
    assert NOT_CURSOR_PAGED <= set(methods), "a streaming export no longer takes offset: drop it from the exclusions"


def _params(request: httpx.Request) -> dict[str, Any]:
    if request.method == "GET":
        return {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
    return json.loads(request.content)


def test_a_cursor_reaches_the_wire():
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_params(request))
        return httpx.Response(
            200,
            json={
                "records": [],
                "pagination": {"offset": 0, "limit": 1, "returned": 0, "total": 0, "mode": "cursor"},
                "next_cursor": None,
            },
        )

    client, _ = make_client(handler)
    client.search_species(smiles="C", cursor="tok")
    assert seen[0]["cursor"] == "tok"


@pytest.mark.parametrize(
    ("name", "filters"),
    [
        ("iter_species", {"smiles": "C"}),
        ("iter_thermo", {"smiles": "C"}),
        ("iter_levels_of_theory", {"method": "x"}),
    ],
)
def test_iterators_follow_the_cursor_and_stop_on_null(name, filters):
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = _params(request)
        calls.append(params)
        last = params.get("cursor") == "c1"
        return httpx.Response(
            200,
            json={
                "records": [{"n": len(calls)}],
                "pagination": {"offset": 0, "limit": 1, "returned": 1, "total": 2, "mode": "cursor"},
                "next_cursor": None if last else "c1",
            },
        )

    client, _ = make_client(handler)
    records = list(getattr(client, name)(limit=1, **filters))
    assert [r["n"] for r in records] == [1, 2]
    assert [c.get("cursor") for c in calls] == [None, "c1"]
