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


def _name(call) -> str:
    """A callable by where it is defined, not by object identity.

    Some tests leave the process in a state where a route's endpoint is a
    different function object on each build (measured on CI: the same
    ``list_calculations``, defined once in the source, as two objects). The
    cache is keyed on identity so it can never serve one for the other; what
    this file must prove is that the *tree* is the same.
    """
    return f"{getattr(call, '__module__', '?')}.{getattr(call, '__qualname__', repr(call))}"


def _dependant_shape(dependant) -> tuple:
    """The whole dependency tree of a route, by callable name."""
    return (
        _name(dependant.call),
        dependant.name,
        tuple(param.name for param in dependant.query_params),
        tuple(param.name for param in dependant.path_params),
        tuple(param.name for param in dependant.body_params),
        tuple(_dependant_shape(sub) for sub in dependant.dependencies),
    )


def _route_shapes(app) -> list[tuple]:
    return [_dependant_shape(route.dependant) for route in app.routes if hasattr(route, "dependant")]


def test_building_the_app_again_never_adds_dependencies_to_a_route(cache_on) -> None:
    """The defect the first version of this cache had.

    ``APIRoute.__init__`` inserts the router's own ``dependencies=[...]`` into
    the dependant it is handed. If the dependant is a shared object, every
    app built after the first doubles up the router dependencies; the OpenAPI
    document looks the same and the suite just gets slower and slower. So
    compare the *dependency trees*, not the documents.
    """
    route_build_cache.uninstall()
    try:
        cold = _route_shapes(create_app())
    finally:
        route_build_cache.install()
        route_build_cache.clear()

    builds = [_route_shapes(create_app()) for _ in range(4)]
    assert cold, "no routes with dependants were found; the comparison is vacuous"
    assert any(shape[5] for shape in cold), "no route has a dependency; the comparison is vacuous"
    for number, shape in enumerate(builds, start=1):
        assert shape == cold, f"build {number} through the cache differs from an uncached build"


def test_a_warm_cache_builds_the_same_routes_in_the_same_order(cache_on) -> None:
    create_app()
    first = create_app()
    second = create_app()

    def shape(app):
        return [(type(r).__name__, getattr(r, "path", None), sorted(getattr(r, "methods", None) or [])) for r in app.routes]

    assert shape(first) == shape(second)
    # Distinct route objects: the cache shares dependants, not routes.
    assert all(a is not b for a, b in zip(first.routes, second.routes, strict=True))


def test_the_second_build_is_mostly_served_from_the_cache(cache_on) -> None:
    create_app()
    hits_before, misses_before = route_build_cache.counters()
    assert misses_before > 0, "the first build should have filled the cache"
    create_app()
    hits, misses = route_build_cache.counters()
    second_hits, second_misses = hits - hits_before, misses - misses_before
    # Not "no misses": a route whose endpoint is a new object each build is
    # a legitimate miss. But the build must be dominated by hits, or the
    # cache is not doing what it exists for.
    assert second_hits > 4 * second_misses, (second_hits, second_misses)


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
