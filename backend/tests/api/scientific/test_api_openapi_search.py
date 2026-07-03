"""OpenAPI exposure tests for the new chemistry-first search endpoints."""

from __future__ import annotations

_NEW_PATHS = {
    "/api/v1/scientific/thermo/search",
    "/api/v1/scientific/kinetics/search",
}


def test_openapi_includes_thermo_and_kinetics_search(client):
    resp = client.get("/openapi.json")
    paths = set(resp.json()["paths"].keys())
    missing = _NEW_PATHS - paths
    assert not missing, f"Missing scientific search paths in OpenAPI: {missing}"


def test_openapi_search_endpoints_support_get_and_post(client):
    resp = client.get("/openapi.json")
    paths = resp.json()["paths"]
    for p in _NEW_PATHS:
        methods = paths[p]
        assert "get" in methods, f"GET missing for {p}"
        assert "post" in methods, f"POST missing for {p}"
