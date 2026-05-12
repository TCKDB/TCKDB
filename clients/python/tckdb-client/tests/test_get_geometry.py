"""Client tests for ``TCKDBClient.get_geometry``.

The client method is a thin wrapper over
``GET /scientific/geometries/{geometry_handle}`` and follows the same
request-shape conventions as the other ``get_*`` detail methods. These
tests verify only the outgoing HTTP request shape; backend behavior is
covered by the API tests.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx

from conftest import make_client


def _ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json=body
        or {
            "request": {"filter": {}, "sort": "", "collapse": "all", "include": []},
            "geometry_ref": "geom_abc",
            "natoms": 0,
            "geom_hash": "deadbeef" * 8,
            "format": "cartesian",
            "coordinate_units": "angstrom",
            "symbols": [],
            "coords": [],
            "atoms": [],
            "xyz_text": None,
            "created_at": "2026-05-11T00:00:00Z",
            "provenance": {"produced_by": [], "used_as_input_by": []},
        },
    )


def _capture():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    return seen, handler


def _qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def test_get_geometry_accepts_public_ref_handle():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.get_geometry("geom_abcdef0123456789")
    assert seen[0].url.path == (
        "/api/v1/scientific/geometries/geom_abcdef0123456789"
    )


def test_get_geometry_accepts_integer_handle():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.get_geometry(42)
    assert seen[0].url.path == "/api/v1/scientific/geometries/42"


def test_get_geometry_serializes_include_tokens():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.get_geometry(
        "geom_xyz", include=["provenance", "internal_ids"]
    )
    qs = _qs(str(seen[0].url))
    assert qs["include"] == ["provenance", "internal_ids"]


def test_get_geometry_returns_parsed_json_dict():
    """The client surfaces the raw response body (no typed wrapping)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(
            {
                "request": {
                    "filter": {},
                    "sort": "",
                    "collapse": "all",
                    "include": [],
                },
                "geometry_ref": "geom_water",
                "natoms": 3,
                "geom_hash": "1" * 64,
                "format": "cartesian",
                "coordinate_units": "angstrom",
                "symbols": ["O", "H", "H"],
                "coords": [[0, 0, 0], [0, 0.76, 0.58], [0, -0.76, 0.58]],
                "atoms": [],
                "xyz_text": None,
                "created_at": "2026-05-11T00:00:00Z",
                "provenance": {"produced_by": [], "used_as_input_by": []},
            }
        )

    client, _ = make_client(handler)
    body = client.get_geometry("geom_water")
    assert body["geometry_ref"] == "geom_water"
    assert body["symbols"] == ["O", "H", "H"]
