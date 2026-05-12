"""OpenAPI exposure tests for the scientific read API surface."""

from __future__ import annotations


_EXPECTED_PATHS = {
    "/api/v1/scientific/species/search",
    "/api/v1/scientific/reactions/search",
    "/api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics",
    "/api/v1/scientific/species-entries/{species_entry_id}/thermo",
    "/api/v1/scientific/reaction-entries/{reaction_entry_id}/full",
    "/api/v1/scientific/geometries/{geometry_handle}",
}


def test_openapi_includes_all_scientific_routes(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = set(resp.json()["paths"].keys())
    missing = _EXPECTED_PATHS - paths
    assert not missing, f"Missing scientific paths in OpenAPI: {missing}"


def test_openapi_reactions_search_supports_get_and_post(client):
    resp = client.get("/openapi.json")
    paths = resp.json()["paths"]
    methods = paths["/api/v1/scientific/reactions/search"]
    assert "get" in methods
    assert "post" in methods


def test_openapi_routes_tagged_scientific(client):
    resp = client.get("/openapi.json")
    paths = resp.json()["paths"]
    for path in _EXPECTED_PATHS:
        ops = paths[path]
        for method, op in ops.items():
            tags = op.get("tags", [])
            assert "scientific" in tags, f"{method.upper()} {path} missing 'scientific' tag"
