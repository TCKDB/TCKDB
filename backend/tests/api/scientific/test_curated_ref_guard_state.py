"""The ref guard never touches exploratory output, is inert outside the enabled families, and costs <= 1 statement.

Across every crawled scientific route, exploratory bytes are identical with the hook removed, wired in (the default)
and enabled everywhere. Curated bytes are identical with the hook removed on routes whose family is not enabled yet,
and equal to the enable-everything output on the routes whose family is. The sweep (``test_curated_ref_sweep.py``)
reports what the guard has still to fix.
"""

from __future__ import annotations

import re

import pytest

from app.services.scientific_read import internal_ids, ref_guard
from tests.api.scientific._curated_sweep_analysis import REGENERATE, fmt, read_baseline, write_baseline
from tests.api.scientific._curated_sweep_crawl import enumerate_routes
from tests.api.scientific._curated_sweep_run import GuardSpy, install_spy, run_pass
from tests.api.scientific._curated_sweep_world import build_sweep_world

_ENABLE_EVERYTHING = frozenset({"/api/v1/scientific"})

#: The design's cost bound: one statement resolves every hidden ref in a response.
EXPECTED_ENABLED_EXTRA_STATEMENTS = 1


#: The only fields that differ between two identical requests: the moment the read was taken. Everything else must be
#: byte-for-byte equal.
_CLOCK = re.compile(r'"(generated_at|taken_at)"\s*:\s*"[^"]*"')


def _bodies(results):
    return {key: (r.status, _CLOCK.sub(r'"\1":"<clock>"', r.text)) for key, r in results.items()}


def _differences(a, b):
    out = []
    for key in sorted(a):
        if a[key] != b.get(key):
            left, right = a[key][1], b.get(key, (None, ""))[1]
            at = next(
                (i for i, (x, y) in enumerate(zip(left, right, strict=False)) if x != y), min(len(left), len(right))
            )
            out.append(
                fmt(
                    key[0],
                    f"{key[1]} (status {a[key][0]} vs {b.get(key, (None,))[0]}; first byte difference at {at}: {left[max(0, at - 20) : at + 40]!r} vs {right[max(0, at - 20) : at + 40]!r})",
                )
            )
    return out


def test_guard_is_inert_by_default_and_costs_at_most_one_statement_when_enabled(
    client, db_session, login_as, monkeypatch
):
    """Only enabled families change on the wire (curated only); a withheld response costs at most one statement.

    * exploratory bytes are identical with the hook removed, wired in (the default) and enabled on every route;
    * curated bytes are identical with the hook removed and wired in (outside the enabled families);
    * with every route enabled, a curated response costs at most one more statement than without the guard;
    * the placements the guard cannot make are listed (ratchet), and it did withhold something somewhere.

    Kills: enabling a family by default (curated bytes change); the guard running under the exploratory profile
    (exploratory bytes change or it costs a statement); a second statement per response; an inert guard that
    never withholds.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    specs = enumerate_routes(client.app)

    def crawl(profile, *, hook=None, enabled=None):
        with monkeypatch.context() as m:
            if hook is not None:
                m.setattr(internal_ids, "withhold_for_current_request", hook)
                spy = GuardSpy()
            else:
                spy = install_spy(m)
            if enabled is not None:
                m.setattr(ref_guard, "_ENABLED_ROUTE_PREFIXES", enabled)
            return run_pass(client, db_session, specs, world, spy, profile=profile)

    def no_hook(payload):
        return None

    crawl("exploratory")  # warm one-time lookups so the statement counts below compare like with like
    crawl("curated")
    exploratory_default = crawl("exploratory")
    exploratory_no_hook = crawl("exploratory", hook=no_hook)
    exploratory_enabled = crawl("exploratory", enabled=_ENABLE_EVERYTHING)
    curated_default = crawl("curated")
    curated_no_hook = crawl("curated", hook=no_hook)
    curated_enabled = crawl("curated", enabled=_ENABLE_EVERYTHING)

    failures: list[str] = []
    assert exploratory_default and curated_default and len(exploratory_default) == len(curated_default)
    def outside_enabled(results):
        return {k: v for k, v in results.items() if not ref_guard.route_matches(k[0][1], ref_guard._ENABLED_ROUTE_PREFIXES)}

    def inside_enabled(results):
        return {k: v for k, v in results.items() if ref_guard.route_matches(k[0][1], ref_guard._ENABLED_ROUTE_PREFIXES)}

    for name, left, right in (
        ("exploratory: hook wired vs removed", exploratory_default, exploratory_no_hook),
        ("exploratory: every route enabled vs removed", exploratory_enabled, exploratory_no_hook),
        # Routes of a family that is not enabled yet change nothing under curated.
        (
            "curated, routes outside the enabled families: hook wired vs removed",
            outside_enabled(curated_default),
            outside_enabled(curated_no_hook),
        ),
        # Routes of an enabled family are exactly what enabling everywhere gives for them.
        (
            "curated, routes inside the enabled families: wired vs every route enabled",
            inside_enabled(curated_default),
            inside_enabled(curated_enabled),
        ),
    ):
        for entry in _differences(_bodies(left), _bodies(right)):
            failures.append(f"{name}: wire output differs at {entry}")
    if not inside_enabled(curated_default) or not outside_enabled(curated_default):
        failures.append("the crawl does not cover both enabled and not-yet-enabled routes: the comparison is vacuous")

    # Exploratory pays nothing: no extra statement and no withholding, even with every route enabled.
    for key, r in exploratory_enabled.items():
        if r.statements != exploratory_default[key].statements:
            failures.append(f"exploratory statement count changed with the guard enabled: {fmt(key[0], key[1])}")
        if r.applied_sites:
            failures.append(f"exploratory response was withheld from: {fmt(key[0], key[1])}")

    # Enabled + curated: at most one extra statement, wherever the response was produced.
    unplaceable: set[str] = set()
    withheld = 0
    for key, r in curated_enabled.items():
        route, label = key
        if r.unplaceable is not None:
            model, path, prefix = r.unplaceable
            unplaceable.add(fmt(route, f"{model}.{path} ({prefix}_)"))
            continue
        withheld += len(r.applied_sites)
        extra = r.statements - curated_no_hook[key].statements
        if extra > EXPECTED_ENABLED_EXTRA_STATEMENTS:
            failures.append(f"{fmt(route, label)} cost {extra} extra statements with the guard enabled")
    if not withheld:
        failures.append("the enabled guard withheld nothing anywhere: it may be inert")

    found = {"unplaceable_when_enabled": sorted(unplaceable)}
    if REGENERATE:
        write_baseline(found)
        print("GUARD-FAILURES", "\n".join(failures))
        pytest.skip("baseline regenerated")
    was = set(read_baseline().get("unplaceable_when_enabled", []))
    for entry in sorted(unplaceable - was):
        failures.append(f"NEW unplaceable_when_enabled: {entry}")
    for entry in sorted(was - unplaceable):
        failures.append(f"FIXED unplaceable_when_enabled (remove it from the baseline): {entry}")
    assert not failures, "\n".join(failures)
