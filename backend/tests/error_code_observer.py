"""Watch what the API actually puts in ``code``, and hold it to the catalogue.

Why a runtime observer and not just a source scan
-------------------------------------------------
:mod:`app.api.code_catalogue` claims to enumerate every code the API can
emit. A claim like that is worth nothing unless something can falsify it,
and a static scan cannot: four mechanisms mint a code from a variable
rather than a literal at the raise site — the ownership guard and
``reconcile_id_ref`` take theirs as a parameter,
``tckdb_schemas.stationary_point`` raises with whichever code its
blocking finding carries, and the integrity handler looks its code up by
PostgreSQL constraint name. A fifth would be a code assembled at request
time, which nothing static could ever see.

This is not hypothetical. The six ``*_handle_conflict`` codes were found
by this observer and by nothing else: the scan cannot see them, and every
document and enum in the repository had been written without them.

So the check is made against what a consumer receives. Every JSON
response with a 4xx/5xx status is recorded as it is built, and the test
that produced an uncatalogued one fails — attributing the miss to the
request that caused it rather than to a suite-wide tally nobody can
debug.

What "uncatalogued" means, and why it is the pair
-------------------------------------------------
The pair, ``(status, code)`` — not the code. The catalogue is keyed that
way because a code can legitimately arrive at two statuses, and the
status is what a client reads its retry advice off. Recording only the
code left the status unchecked, and two entries were wrong for years
without anything noticing. The recorder always held pairs; only the
comparison was narrow.

This is deliberately not a tally with a floor ("at least N codes were
seen"). A floor would be a second thing to keep in step with the suite,
and it would go green for the wrong reason the day a gate's selection
changed. The falsifiability comes from the check running on every
response the whole suite produces, in all three gates.

Cost is one ``isinstance`` and a set lookup per error response; nothing
is recorded for 2xx.
"""

from __future__ import annotations

from app.api.code_catalogue import CATALOGUE, STATUS_FALLBACK_PATTERN

#: ``(status, code)`` seen since the last drain. Bounded by the number of
#: distinct pairs, not by the number of requests.
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


def catalogued_pairs() -> frozenset[tuple[int, str]]:
    """Every ``(status, code)`` the catalogue says the API can produce.

    The catalogue is keyed on the pair, not on the code: a claim stated at
    the wire boundary and again as a check constraint gets two entries, at
    422 and at 409, because the retry advice differs and a client reads it
    off the status.
    """
    return frozenset((entry.status, entry.code) for entry in CATALOGUE)


def statuses_catalogued_for(code: str) -> tuple[int, ...]:
    """The statuses the catalogue records for *code*, ascending.

    Empty when the code is not catalogued at all, which is what lets the
    teardown hook tell "no entry" apart from "entry at the wrong status".
    """
    return tuple(sorted(entry.status for entry in CATALOGUE if entry.code == code))


def drain_unlisted() -> list[tuple[int, str]]:
    """Return, and forget, the observed pairs the catalogue does not list.

    The check is on the ``(status, code)`` *pair*, not the code alone.
    Status is contract: a client branching on 409 versus 422 to decide
    whether the write reached the database gets it wrong when the
    catalogue records the wrong one, and the catalogue generates both the
    client's enum and its ``REJECTION_STATUSES``. Checking only the code
    left that half unwatched -- ``curation_policy_version_conflict`` and
    ``release_tag_taken`` were both recorded at 422 and both arrive at
    409, and nothing in the suite could say so.

    Widening the tuple does not, by itself, catch those two: neither was
    emitted by any test, and the pairs the suite *does* produce all agreed
    with the catalogue when this was measured. The tests that provoke them
    on the wire are what turns this from a guard over an untested claim
    into one that would have failed. Both halves were needed.

    Draining rather than accumulating is what keeps the failure attached
    to the request that caused it: without it the first uncatalogued code
    would fail every subsequent test in the process as well, and the
    real culprit would be whichever test happened to run first.
    """
    seen = sorted(_SEEN)
    _SEEN.clear()
    known = catalogued_pairs()
    return [
        (status, code)
        for status, code in seen
        if (status, code) not in known and not STATUS_FALLBACK_PATTERN.match(code)
    ]


def explain(status: int, code: str) -> str:
    """One line naming which half of the pair the catalogue disagrees with.

    The two failures need opposite fixes -- add an entry, or correct the
    status on the entry that exists -- and a message that says only
    "not listed" sends a reader looking for the wrong one.
    """
    catalogued = statuses_catalogued_for(code)
    if not catalogued:
        return f"HTTP {status} {code!r}: no catalogue entry at all"
    return (
        f"HTTP {status} {code!r}: catalogued only at "
        + ", ".join(str(value) for value in catalogued)
    )
