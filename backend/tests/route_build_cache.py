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
* A ``Dependant`` is a description of a callable's parameters. It is read,
  never written, once the route exists, and it holds no reference to an app.
* ``dependency_overrides`` -- the thing tests do mutate -- lives on the
  ``FastAPI`` instance and is consulted when a request is *solved*
  (``solve_dependencies`` looks the callable up in the app's overrides and,
  if it is overridden, builds a fresh dependant for the replacement). It is
  not part of the cached value, so an override on one app is invisible to
  another. ``tests/test_route_build_cache.py`` pins that.
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

import os
from typing import Any, Callable

_INSTALLED = False
_CACHE: dict[tuple[Any, ...], Any] = {}
_ORIGINAL: Callable[..., Any] | None = None


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
        try:
            return _CACHE[key]
        except KeyError:
            result = _CACHE[key] = original(**kwargs)
            return result

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
