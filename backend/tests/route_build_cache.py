"""Build the route table once per process instead of once per test.

Why this exists
---------------
The ``client`` fixture calls ``create_app()`` for every test, on purpose: an
app is configured from ``settings`` at construction, and a good many tests
monkeypatch a setting (CORS origins, docs exposure, rate limiting, the body
caps) *and then* build their own app. That per-test construction is a
property worth keeping, and this module does not touch it.

What it does touch is the price. Measured on the CI runner's own profile
(``--junitxml`` of the three gates, 2026-10-24), an API test cost ~1.4 s on
average and ~1 s of that was ``create_app()``: ``include_router`` re-creates
all ~280 ``APIRoute`` objects, and almost all of the time inside that is
``fastapi.dependencies.utils.get_dependant`` walking each endpoint's
signature and building a pydantic ``TypeAdapter`` for every parameter
(~2,250 of them). The answer is the same every time -- it is a pure function
of the endpoint callable and the route path.

So ``get_dependant`` is memoised here, for the test process only. With a warm
cache ``create_app()`` drops from ~0.5 s to ~0.07 s locally.

Why it does not change what a test sees
---------------------------------------
* A ``Dependant`` is a description of a callable's parameters and holds no
  reference to an app. Routing *does* write to the one it is given (see
  ``cached_get_dependant``), so what is handed out is always a copy with its
  own ``dependencies`` list, and the cached original is never touched.
* ``dependency_overrides`` -- the thing tests do mutate -- lives on the
  ``FastAPI`` instance and is consulted when a request is *solved*
  (``solve_dependencies`` looks the callable up in the app's overrides and,
  if it is overridden, builds a fresh dependant for the replacement). It is
  not part of the cached value, so an override on one app is invisible to
  another. ``tests/test_route_build_cache.py`` pins that, and pins that
  building an app N times gives routes identical to building it once.
* The key is the full argument tuple, including the endpoint callable by
  identity. A test that defines its own endpoint gets its own entry; a
  failed construction raises out of the wrapped function and is never
  cached.
* ``tests/test_route_build_cache.py`` also asserts that an app built through
  a warm cache serves a byte-identical OpenAPI document to one built with
  the cache off, which is the contract the routes exist to produce.

Set ``TCKDB_TEST_ROUTE_CACHE=0`` to run without it (bisecting, or checking
that a failure is not this module's doing).
"""

from __future__ import annotations

import copy
import os
from typing import Any, Callable

_INSTALLED = False
_CACHE: dict[tuple[Any, ...], Any] = {}
_ORIGINAL: Callable[..., Any] | None = None
_HITS = 0
_MISSES = 0


def _key(kwargs: dict[str, Any]) -> tuple[Any, ...] | None:
    """The call's identity, or ``None`` if it cannot be made hashable.

    ``get_dependant`` is keyword-only. Every argument is part of the key, so
    a new parameter FastAPI adds in a later release changes the key rather
    than being silently ignored.
    """
    parts: list[Any] = []
    for name in sorted(kwargs):
        value = kwargs[name]
        if isinstance(value, list):
            value = tuple(value)
        parts.append((name, value))
    key = tuple(parts)
    try:
        hash(key)
    except TypeError:
        return None
    return key


def enabled() -> bool:
    return _INSTALLED


def clear() -> None:
    _CACHE.clear()


def counters() -> tuple[int, int]:
    """``(hits, misses)`` since the cache was installed."""
    return _HITS, _MISSES


def install() -> None:
    """Memoise ``get_dependant`` in the modules that call it. Idempotent."""
    global _INSTALLED, _ORIGINAL
    if _INSTALLED or os.environ.get("TCKDB_TEST_ROUTE_CACHE", "1") == "0":
        return

    import fastapi.dependencies.utils as dependency_utils
    import fastapi.routing as routing

    original = dependency_utils.get_dependant
    # ``routing`` imports the name, so it has its own binding to patch; the
    # recursion inside ``dependency_utils`` resolves the module global.
    if routing.get_dependant is not original:
        return  # FastAPI changed shape; run uncached rather than guess.

    def cached_get_dependant(**kwargs: Any) -> Any:
        key = _key(kwargs)
        if key is None:
            return original(**kwargs)
        global _HITS, _MISSES
        try:
            pristine = _CACHE[key]
            _HITS += 1
        except KeyError:
            _MISSES += 1
            pristine = _CACHE[key] = original(**kwargs)
        # Always a copy, never the cached object itself -- including on the
        # first call, which would otherwise hand the caller the cache entry.
        # ``APIRoute.__init__`` *writes* to the dependant it is given: it
        # inserts the router's ``dependencies=[...]`` at the front of
        # ``dependant.dependencies``. A shared object would collect those
        # inserts from every app ever built, so the Nth app's routes would
        # resolve N copies of each router dependency. (The first version of
        # this module shared it, and the API gate stopped finishing: the
        # OpenAPI document does not show duplicates, which is why no
        # contract check noticed.) The list is the only thing routing
        # mutates, so it is the only thing copied.
        clone = copy.copy(pristine)
        clone.dependencies = list(pristine.dependencies)
        return clone

    _ORIGINAL = original
    dependency_utils.get_dependant = cached_get_dependant  # type: ignore[assignment]
    routing.get_dependant = cached_get_dependant  # type: ignore[assignment]
    _INSTALLED = True


def uninstall() -> None:
    """Restore the real function. For the test that compares against it."""
    global _INSTALLED, _ORIGINAL
    if not _INSTALLED or _ORIGINAL is None:
        return

    import fastapi.dependencies.utils as dependency_utils
    import fastapi.routing as routing

    dependency_utils.get_dependant = _ORIGINAL  # type: ignore[assignment]
    routing.get_dependant = _ORIGINAL  # type: ignore[assignment]
    _CACHE.clear()
    _ORIGINAL = None
    _INSTALLED = False
