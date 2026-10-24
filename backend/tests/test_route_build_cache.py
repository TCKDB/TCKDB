"""The route-build cache must be invisible to everything it speeds up.

See ``tests/route_build_cache.py``. These are the properties that make
caching ``get_dependant`` safe, stated as tests so a FastAPI upgrade that
breaks one fails here rather than as a mystery in some unrelated API file.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi import Depends, FastAPI
from fastapi._compat import ModelField
from fastapi.dependencies.models import Dependant
from fastapi.testclient import TestClient

from app.api.app import create_app
from tests import route_build_cache


@pytest.fixture
def cache_on():
    """Run with the cache installed whatever the environment says."""
    was_installed = route_build_cache.enabled()
    # force: the nightly sets TCKDB_TEST_ROUTE_CACHE=0 so its suite builds
    # fresh apps, and these tests are exactly what should still run there.
    route_build_cache.install(force=True)
    if not route_build_cache.enabled():
        pytest.skip("an unfamiliar FastAPI: the cache declined to install")
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
        route_build_cache.install(force=True)

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


def _deep(value, depth: int = 0):
    """Everything comparable about a dependant, recursively and by value.

    Walks every dataclass field of ``Dependant`` (so a field FastAPI adds
    later is compared without anyone remembering to list it), turns
    callables into names and ``ModelField`` into what defines it.
    """
    assert depth < 40, "dependant tree is implausibly deep"
    if isinstance(value, Dependant):
        shape = {f.name: _deep(getattr(value, f.name), depth + 1) for f in dataclasses.fields(Dependant)}
        # Derived but load-bearing; computed lazily on the dependant.
        shape["computed_scope"] = value.computed_scope
        return ("Dependant", tuple(sorted(shape.items())))
    if isinstance(value, ModelField):
        return (
            "ModelField",
            value.name,
            getattr(value, "alias", None),
            repr(value.field_info.annotation),
            value.field_info.is_required(),
            repr(value.field_info.default),
        )
    if isinstance(value, list | tuple):
        return tuple(_deep(item, depth + 1) for item in value)
    if callable(value):
        return _name(value)
    return value


def _route_shape(route) -> tuple:
    return (
        route.path,
        tuple(sorted(route.methods or ())),
        _deep(route.dependant),
        _deep(route._flat_dependant),
        _deep(route.body_field) if route.body_field is not None else None,
        route._embed_body_fields,
    )


def _route_shapes(app) -> list[tuple]:
    return [_route_shape(route) for route in app.routes if hasattr(route, "dependant")]


def _flatten(shape) -> list:
    out: list = []
    stack = [shape]
    while stack:
        node = stack.pop()
        if isinstance(node, tuple):
            stack.extend(node)
        else:
            out.append(node)
    return out


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
        route_build_cache.install(force=True)
        route_build_cache.clear()

    builds = [_route_shapes(create_app()) for _ in range(4)]
    assert cold, "no routes with dependants were found; the comparison is vacuous"
    # Non-vacuity: the comparison must be looking at the things it claims to.
    assert any(shape[2][1] and dict(shape[2][1])["dependencies"] for shape in cold), "no route has a dependency"
    assert any(dict(shape[2][1])["header_params"] for shape in cold), "no route reads a header; header_params unchecked"
    assert any(dict(shape[2][1])["query_params"] for shape in cold), "no route reads a query parameter"
    assert any(shape[4] is not None for shape in cold), "no route has a body field"
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


# Module level on purpose: a function defined inside the builder is a new
# object per call, so its cache key would never repeat and the override test
# below would pass without ever touching a cached dependant.
def _probe(value: str = Depends(_dependency)) -> dict[str, str]:
    return {"value": value}


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_api_route("/probe", _probe, methods=["GET"])
    return app


def test_a_dependency_override_on_one_app_does_not_reach_another(cache_on) -> None:
    _build_app()  # warm: the apps below must be built from cache hits
    hits_before, _ = route_build_cache.counters()
    first = _build_app()
    second = _build_app()
    assert route_build_cache.counters()[0] > hits_before, "the apps were not built from the cache"
    first.dependency_overrides[_dependency] = lambda: "overridden"

    with TestClient(first) as one, TestClient(second) as two:
        assert one.get("/probe").json() == {"value": "overridden"}
        assert two.get("/probe").json() == {"value": "real"}

    # And the other way round, after a rebuild from the warm cache.
    third = _build_app()
    with TestClient(third) as three:
        assert three.get("/probe").json() == {"value": "real"}


def test_sub_dependants_are_not_mutated_after_construction(cache_on) -> None:
    """Why sharing sub-dependants between apps is safe on this FastAPI.

    Only the top dependant of a route is copied by the cache; the
    dependants nested in it are shared by every app. That is safe while
    nothing writes to a nested dependant after ``get_dependant`` returns. If
    a FastAPI upgrade starts to (as ``APIRoute.__init__`` already does to the
    top one), the deep state of the nested dependants will drift between
    builds and this fails.
    """
    create_app()
    first = create_app()
    snapshot = [_deep(route.dependant) for route in first.routes if hasattr(route, "dependant")]
    for _ in range(3):
        create_app()
    after = [_deep(route.dependant) for route in first.routes if hasattr(route, "dependant")]
    assert after == snapshot, "building more apps changed an existing app's dependants"
