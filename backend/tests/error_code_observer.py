"""Watch what the API actually puts in ``code``, and hold it to the catalogue.

Why a runtime observer and not just a source scan
-------------------------------------------------
:mod:`app.api.code_catalogue` claims to enumerate every code the API can
emit. A claim like that is worth nothing unless something can falsify it,
and a static scan cannot: three mechanisms mint a code from a variable
rather than a literal at the raise site — the ownership guard takes its
code as a parameter, ``tckdb_schemas.stationary_point`` raises with
whichever code its blocking finding carries, and the integrity handler
looks its code up by PostgreSQL constraint name. A fourth would be a code
assembled at request time, which nothing static could ever see.

So the check is made against what a consumer receives. Every JSON
response with a 4xx/5xx status is recorded as it is built, and the test
that produced an uncatalogued one fails — attributing the miss to the
request that caused it rather than to a suite-wide tally nobody can
debug.

This is deliberately not a tally with a floor ("at least N codes were
seen"). A floor would be a second thing to keep in step with the suite,
and it would go green for the wrong reason the day a gate's selection
changed. The falsifiability comes from the check running on every
response the whole suite produces, in all three gates.

Cost is one ``isinstance`` and a set lookup per error response; nothing
is recorded for 2xx.
"""

from __future__ import annotations

from app.api.code_catalogue import STATUS_FALLBACK_PATTERN, catalogued_codes

#: ``(status, code)`` seen since the last drain. Bounded by the number of
#: distinct codes, not by the number of requests.
_SEEN: set[tuple[int, str]] = set()

_INSTALLED = False


def install() -> None:
    """Patch :class:`starlette.responses.JSONResponse` to record error codes.

    Idempotent: pytest imports conftest once per process, but a plugin
    reload or a nested session must not stack wrappers.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from starlette.responses import JSONResponse

    original = JSONResponse.__init__

    def recording_init(self, content=None, status_code=200, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(status_code, int) and status_code >= 400:
            if isinstance(content, dict):
                code = content.get("code")
                if isinstance(code, str) and code:
                    _SEEN.add((status_code, code))
        return original(self, content, status_code, *args, **kwargs)

    JSONResponse.__init__ = recording_init  # type: ignore[method-assign]
    _INSTALLED = True


def drain_unlisted() -> list[tuple[int, str]]:
    """Return, and forget, the observed codes the catalogue does not list.

    Draining rather than accumulating is what keeps the failure attached
    to the request that caused it: without it the first uncatalogued code
    would fail every subsequent test in the process as well, and the
    real culprit would be whichever test happened to run first.
    """
    seen = sorted(_SEEN)
    _SEEN.clear()
    known = catalogued_codes()
    return [
        (status, code)
        for status, code in seen
        if code not in known and not STATUS_FALLBACK_PATTERN.match(code)
    ]
