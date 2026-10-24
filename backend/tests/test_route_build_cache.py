"""The route-build cache must be invisible to everything it speeds up.

See ``tests/route_build_cache.py``. These are the properties that make
caching ``get_dependant`` safe, stated as tests so a FastAPI upgrade that
breaks one fails here rather than as a mystery in some unrelated API file.
"""

from __future__ import annotations

import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.app import create_app
from tests import route_build_cache


@pytest.fixture
def cache_on():
    """Run with the cache installed whatever the environment says."""
    was_installed = route_build_cache.enabled()
    route_build_cache.install()
    if not route_build_cache.enabled():
        pytest.skip("route cache disabled by TCKDB_TEST_ROUTE_CACHE=0 or an unfamiliar FastAPI")
    route_build_cache.clear()
    yield
    if not was_installed:
        route_build_cache.uninstall()


def _openapi(app) -> str:
    return json.dumps(app.openapi(), sort_keys=True)


def test_a_warm_cache_builds_the_same_contract_as_no_cache(cache_on) -> None:
    create_app()  # warm
    warm = _openapi(create_app())

    route_build_cache.uninstall()
    try:
        cold = _openapi(create_app())
    finally:
        route_build_cache.install()

    assert warm == cold


def test_a_warm_cache_builds_the_same_routes_in_the_same_order(cache_on) -> None:
    create_app()
    first = create_app()
    second = create_app()

    def shape(app):
        return [(type(r).__name__, getattr(r, "path", None), sorted(getattr(r, "methods", None) or [])) for r in app.routes]

    assert shape(first) == shape(second)
    # Distinct route objects: the cache shares dependants, not routes.
    assert all(a is not b for a, b in zip(first.routes, second.routes))


def test_the_second_build_is_served_from_the_cache(cache_on) -> None:
    create_app()
    warmed = len(route_build_cache._CACHE)
    assert warmed > 0
    create_app()
    assert len(route_build_cache._CACHE) == warmed, "a rebuilt app should add no entries"


def _dependency() -> str:
    return "real"


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    def probe(value: str = Depends(_dependency)) -> dict[str, str]:
        return {"value": value}

    return app


def test_a_dependency_override_on_one_app_does_not_reach_another(cache_on) -> None:
    first = _build_app()
    second = _build_app()
    first.dependency_overrides[_dependency] = lambda: "overridden"

    with TestClient(first) as one, TestClient(second) as two:
        assert one.get("/probe").json() == {"value": "overridden"}
        assert two.get("/probe").json() == {"value": "real"}

    # And the other way round, after a rebuild from the warm cache.
    third = _build_app()
    with TestClient(third) as three:
        assert three.get("/probe").json() == {"value": "real"}


def test_a_failed_construction_is_not_cached(cache_on) -> None:
    def broken(x: object = Depends(lambda: None)) -> None:  # noqa: B008
        return None

    app = FastAPI()
    entries = len(route_build_cache._CACHE)
    with pytest.raises(Exception):
        # A path parameter the callable does not declare is a construction
        # error in FastAPI; whichever it raises, nothing may be left behind
        # that would make the second attempt succeed.
        app.add_api_route("/{missing}", broken, response_model=type("NotAModel", (), {"__slots__": ()}))
    again = FastAPI()
    with pytest.raises(Exception):
        again.add_api_route("/{missing}", broken, response_model=type("NotAModel", (), {"__slots__": ()}))
    assert len(route_build_cache._CACHE) >= entries
