"""``collapse`` and ``selection_policy`` are refused on EVERY scientific operation, whatever the route.

Enumerated from the live OpenAPI document, not listed by hand, so a route added tomorrow is covered the day it is
added. Each operation is sent the parameter in the place it takes input (the query string for a GET, the query string
and the JSON body for a POST); the guard runs before path validation, so a placeholder path value is enough.
"""

from __future__ import annotations

import re

import pytest

PREFIX = "/api/v1/scientific"


def _operations(app):
    paths = app.openapi()["paths"]
    for path, item in sorted(paths.items()):
        if not path.startswith(PREFIX):
            continue
        for method in ("get", "post"):
            if method in item:
                yield method, path


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def test_the_enumeration_reaches_the_whole_scientific_surface(client):
    operations = list(_operations(client.app))
    assert len(operations) > 100, f"only {len(operations)} scientific operations found: the sweep proves too little"
    assert ("get", f"{PREFIX}/reactions/browse") in operations
    assert ("get", f"{PREFIX}/species/browse") in operations


@pytest.mark.parametrize("parameter", ["collapse", "selection_policy"])
def test_every_scientific_operation_refuses_a_picking_parameter(client, parameter):
    """Kills: a route (``/reactions/browse`` was one) that ignores ``collapse=first`` while its neighbours refuse it."""
    failures = []
    for method, path in _operations(client.app):
        url = _concrete(path)
        if method == "get":
            response = client.get(url, params={parameter: "first"})
        else:
            response = client.post(url, params={parameter: "first"}, json={})
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        if response.status_code != 422 or body.get("code") != "browse_parameter_removed":
            failures.append((method.upper(), path, response.status_code, body.get("code")))
        elif method == "post":
            in_body = client.post(url, json={parameter: "first"})
            in_body_body = in_body.json() if in_body.headers.get("content-type", "").startswith("application/json") else {}
            if in_body.status_code != 422 or in_body_body.get("code") != "browse_parameter_removed":
                failures.append((method.upper() + " body", path, in_body.status_code, in_body_body.get("code")))
    assert not failures, failures
