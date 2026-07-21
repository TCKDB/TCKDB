"""Live request-boundary coverage for scientific kinetics pressure filters."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/scientific/reaction-entries/999999/kinetics",
        "/api/v1/scientific/kinetics/search?reactants=NO_MATCH",
    ],
)
@pytest.mark.parametrize("field", ["pressure_bar", "pressure"])
@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_get_pressure_rejects_nonfinite_values(client, path, field, value):
    separator = "&" if "?" in path else "?"
    response = client.get(f"{path}{separator}{field}={value}")

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_error"


@pytest.mark.parametrize("field", ["pressure_bar", "pressure"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_post_pressure_rejects_nonfinite_json_numbers(client, field, value):
    response = client.post(
        "/api/v1/scientific/kinetics/search",
        content=f'{{"reactants":["NO_MATCH"],"{field}":{value}}}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_error"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/scientific/reaction-entries/999999/kinetics"),
        ("get", "/api/v1/scientific/kinetics/search"),
        ("post", "/api/v1/scientific/kinetics/search"),
    ],
)
def test_live_pressure_alias_comparison_is_exact(client, method, path):
    payload = {"pressure_bar": 1.0, "pressure": 1.0000000000005}
    if method == "get":
        response = client.get(path, params={"reactants": "NO_MATCH", **payload})
    else:
        response = client.post(path, json={"reactants": ["NO_MATCH"], **payload})

    assert response.status_code == 422
    assert response.json()["code"] == "pressure_alias_conflict"
