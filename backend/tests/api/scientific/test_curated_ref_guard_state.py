"""The ref guard never touches exploratory output, withholds under curated everywhere, and costs <= 1 statement.

Across every crawled scientific route, exploratory bytes are identical with the hook removed and with it wired in (the
default), at the same statement count. Under curated the guard costs at most one statement per response, withholds
something, and never fails closed on a crawled route (no ``RefGuardUnplaceable``). The sweep
(``test_curated_ref_sweep.py``) checks what it leaves on the wire.
"""

from __future__ import annotations

import re

from app.services.scientific_read import internal_ids
from tests.api.scientific._curated_sweep_analysis import fmt
from tests.api.scientific._curated_sweep_crawl import enumerate_routes
from tests.api.scientific._curated_sweep_run import GuardSpy, install_spy, run_pass
from tests.api.scientific._curated_sweep_world import build_sweep_world

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


def _statement_report(wired, removed) -> str:
    """Both statement lists, side by side, so a failure names its cause instead of only a route.

    A count that differs between two identical requests is either the guard (a statement only one side issued) or
    noise (a lazy load, a cache that was warm on one side). Printing the statements that are in one list and not the
    other, plus both full lists and the response status of each side, makes the two distinguishable from the log.
    """
    only_wired = [t for t in wired.statement_texts if t not in removed.statement_texts]
    only_removed = [t for t in removed.statement_texts if t not in wired.statement_texts]

    def short(texts):
        return [" ".join(t.split())[:240] for t in texts]

    lines = [
        f"    statements: hook wired={wired.statements}, hook removed={removed.statements}",
        f"    response status: hook wired={wired.status}, hook removed={removed.status}",
        f"    only with the hook wired ({len(only_wired)}): {short(only_wired)}",
        f"    only with the hook removed ({len(only_removed)}): {short(only_removed)}",
        f"    all, hook wired: {short(wired.statement_texts)}",
        f"    all, hook removed: {short(removed.statement_texts)}",
    ]
    return "\n".join(lines)


def test_guard_leaves_exploratory_alone_and_costs_at_most_one_statement(client, db_session, login_as, monkeypatch):
    """Exploratory is byte-identical with and without the hook; a curated response costs at most one more statement.

    * exploratory bytes and statement counts are identical with the hook removed and wired in (the default);
    * a curated response costs at most one more statement than without the guard;
    * no crawled curated route fails closed, and the guard did withhold something somewhere.

    Kills: the guard running under the exploratory profile (exploratory bytes change or it costs a statement); a
    second statement per response; an inert guard that never withholds; a route whose registry has a gap.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    specs = enumerate_routes(client.app)

    def crawl(profile, *, hook=None):
        with monkeypatch.context() as m:
            if hook is not None:
                m.setattr(internal_ids, "withhold_for_current_request", hook)
                spy = GuardSpy()
            else:
                spy = install_spy(m)
            return run_pass(client, db_session, specs, world, spy, profile=profile)

    def no_hook(payload):
        return None

    crawl("exploratory")  # warm one-time lookups so the statement counts below compare like with like
    crawl("curated")
    exploratory_default = crawl("exploratory")
    exploratory_no_hook = crawl("exploratory", hook=no_hook)
    curated_default = crawl("curated")
    curated_no_hook = crawl("curated", hook=no_hook)

    failures: list[str] = []
    assert exploratory_default and curated_default and len(exploratory_default) == len(curated_default)
    for entry in _differences(_bodies(exploratory_default), _bodies(exploratory_no_hook)):
        failures.append(f"exploratory: hook wired vs removed: wire output differs at {entry}")

    # Exploratory pays nothing: no extra statement and no withholding.
    for key, r in exploratory_default.items():
        if r.statements != exploratory_no_hook[key].statements:
            failures.append(
                f"exploratory statement count changed with the guard wired in: {fmt(key[0], key[1])}\n"
                + _statement_report(r, exploratory_no_hook[key])
            )
        if r.applied_sites:
            failures.append(f"exploratory response was withheld from: {fmt(key[0], key[1])}")

    # Curated: at most one extra statement, wherever the response was produced.
    withheld = 0
    for key, r in curated_default.items():
        route, label = key
        if r.unplaceable is not None:
            model, path, prefix = r.unplaceable
            failures.append(fmt(route, f"unplaceable: {model}.{path} ({prefix}_)"))
            continue
        withheld += len(r.applied_sites)
        extra = r.statements - curated_no_hook[key].statements
        if extra > EXPECTED_ENABLED_EXTRA_STATEMENTS:
            failures.append(f"{fmt(route, label)} cost {extra} extra statements with the guard")
    if not withheld:
        failures.append("the guard withheld nothing anywhere: it may be inert")
    assert not failures, "\n".join(failures)


def test_a_statement_count_difference_is_reported_with_the_differing_statements():
    """The diagnostic itself: a failure of the exploratory statement check must name the statement that differs."""
    from tests.api.scientific._curated_sweep_crawl import Result

    wired = Result(200, "{}", statements=3, statement_texts=["SELECT 1", "SELECT hidden_ref FROM record_review", "SELECT 2"])
    removed = Result(200, "{}", statements=2, statement_texts=["SELECT 1", "SELECT 2"])

    report = _statement_report(wired, removed)

    assert "hook wired=3, hook removed=2" in report
    assert "only with the hook wired (1): ['SELECT hidden_ref FROM record_review']" in report
    assert "only with the hook removed (0): []" in report
    assert "response status: hook wired=200, hook removed=200" in report
