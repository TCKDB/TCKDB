"""A warning must reach the phone without waking anybody.

THE FAILURE THIS PREVENTS
    ``/status`` grew a ``warnings`` array for things that are true but not
    yet broken -- today, an object store whose remaining room has fallen
    below the largest artifact TCKDB accepts. The obvious way to surface it
    was to add it to ``degraded``. That would have been a mistake with a
    long half-life: this checker pushes ``degraded`` at ``Priority: high``
    and exits 1, so a store at 90 % would have paged an operator and marked
    the systemd unit's run unhealthy while every upload still worked.

    An alert that fires when nothing is broken is an alert you learn to
    swipe away, and you swipe the real ones away with it. So the tests here
    are as much about what does NOT happen -- no urgent priority, no
    non-zero exit, no ``degraded`` -- as about the push that does.

EDGE-TRIGGERED, ON ITS OWN AXIS
    The verdict already notifies only on change. A warning needs the same
    discipline for the same reason (a five-minute timer, a condition that
    can stand for weeks) but on a *separate* state file, because the two
    move independently: a store can start warning while the deployment
    stays healthy, and stop warning while it stays degraded.

BOTH PARSE PATHS
    The ``jq``-free fallback is exercised alongside, as in
    ``test_alert_check_degraded.py``. It is a separately written parser and
    the one that has been wrong before. It cannot read an array of objects,
    so it claims only presence -- and the three-way distinction it has to
    get right is that an ABSENT ``warnings`` field (an older build) and an
    EMPTY one are both silence.
"""

from __future__ import annotations

import pytest


def _status(*, warnings: list[dict] | None, degraded: list[str] | None = None) -> dict:
    """A ``/status`` body with the requested warnings and degradation."""
    degraded = degraded or []
    body: dict = {
        "status": "degraded" if degraded else "ok",
        "degraded": degraded,
        "components": {
            "database": {"healthy": True, "alembic_revision": "abc123", "reason": None},
            "worker": {"healthy": True, "reason": None},
            "artifact_storage": {
                "healthy": not degraded,
                "reachable": True,
                "reason": "cannot reach object store" if degraded else None,
                "warnings": warnings or [],
            },
        },
    }
    if warnings is not None:
        body["warnings"] = [
            {"component": "artifact_storage", **warning} for warning in warnings
        ]
    return body


HEADROOM_WARNING = {
    "code": "artifact_storage_headroom_low",
    "summary": (
        "artifact storage has 8,388,608 bytes of headroom, less than the "
        "52,428,800-byte artifact TCKDB will accept, so a full-size upload "
        "may be refused."
    ),
    "headroom_bytes": 8_388_608,
    "source": "free_space",
}


def _pushes(fake_host, title: str) -> list:
    return [
        request
        for request in fake_host.paths("/ntfy")
        if request.headers.get("title") == title
    ]


def _priorities(fake_host, title: str) -> list[str]:
    return [r.headers.get("priority") for r in _pushes(fake_host, title)]


@pytest.mark.parametrize("drop_jq", [False, True], ids=["with-jq", "without-jq"])
def test_a_warning_pushes_at_low_priority_and_the_run_stays_green(
    fake_host, run_alert_check, drop_jq
):
    """The whole contract, on both parse paths.

    ``Priority: low`` and exit 0. ``urgent`` is what this script sends for
    an unreachable deployment and ``high`` for a degraded one; a warning
    must be neither, or it competes with the alerts that mean something.
    """
    fake_host.set_status(_status(warnings=[HEADROOM_WARNING]))
    proc = run_alert_check(drop_jq=drop_jq)

    assert proc.returncode == 0, (
        f"a warning turned a healthy deployment into a failed check: {proc.stderr}"
    )
    assert "status=ok" in proc.stdout

    pushes = _pushes(fake_host, "TCKDB warning")
    assert len(pushes) == 1, [p.headers.get("title") for p in fake_host.paths("/ntfy")]
    assert pushes[0].headers.get("priority") == "low"
    assert pushes[0].headers.get("priority") not in ("urgent", "high")
    if not drop_jq:
        assert "8,388,608 bytes of headroom" in pushes[0].body
    else:
        # The fallback cannot parse an array of objects and does not
        # pretend to: it says where to look instead of inventing detail.
        assert "install jq" in pushes[0].body


@pytest.mark.parametrize("drop_jq", [False, True], ids=["with-jq", "without-jq"])
def test_a_warning_never_reaches_the_degraded_channel(
    fake_host, run_alert_check, drop_jq
):
    """No "TCKDB degraded" push, and no degraded verdict in the journal.

    Asserted separately from the exit code because they are two different
    ways to get this wrong: parsing ``warnings`` into the ``degraded``
    variable would page; deciding the verdict on ``warnings`` would redden
    the unit. Neither may happen.
    """
    fake_host.set_status(_status(warnings=[HEADROOM_WARNING]))
    proc = run_alert_check(drop_jq=drop_jq)

    assert _pushes(fake_host, "TCKDB degraded") == []
    assert "status=degraded" not in proc.stdout
    assert proc.returncode == 0


@pytest.mark.parametrize("drop_jq", [False, True], ids=["with-jq", "without-jq"])
def test_no_warnings_is_silence_on_both_parse_paths(
    fake_host, run_alert_check, drop_jq
):
    """An empty array must not manufacture a push.

    The fallback parser is the risk here: it matches on the literal text of
    the response, and a pattern that fired on ``"warnings":[]`` would push
    every five minutes forever on a perfectly healthy deployment.
    """
    fake_host.set_status(_status(warnings=[]))
    proc = run_alert_check(drop_jq=drop_jq)

    assert proc.returncode == 0
    assert _pushes(fake_host, "TCKDB warning") == []


@pytest.mark.parametrize("drop_jq", [False, True], ids=["with-jq", "without-jq"])
def test_an_older_build_without_the_field_is_also_silence(
    fake_host, run_alert_check, drop_jq
):
    """A deployment predating ``warnings`` is not a warning.

    This checker is copied onto the host and outlives the build it watches,
    so the two versions disagreeing is the normal state during a deploy. An
    absent field must read as "nothing to say", never as "unparseable" and
    never as a push.
    """
    fake_host.set_status(_status(warnings=None))
    proc = run_alert_check(drop_jq=drop_jq)

    assert proc.returncode == 0
    assert _pushes(fake_host, "TCKDB warning") == []


def test_a_standing_warning_is_pushed_once_and_then_stops(fake_host, run_alert_check):
    """Five minutes times a week is 2,016 identical pushes.

    Edge-triggered, like the verdict. The condition this warns about --
    a disk filling up -- can stand for days before anybody acts, which is
    exactly the shape that turns a useful channel into a muted one.
    """
    fake_host.set_status(_status(warnings=[HEADROOM_WARNING]))

    run_alert_check()
    assert len(_pushes(fake_host, "TCKDB warning")) == 1

    run_alert_check()
    run_alert_check()
    assert len(_pushes(fake_host, "TCKDB warning")) == 1, (
        "a standing warning was re-pushed on every poll"
    )


def test_a_changed_warning_is_pushed_again(fake_host, run_alert_check):
    """Edge-triggered is not "said once, ever".

    Headroom falling further, or a second component starting to warn, is
    new information. Suppressing it would be the opposite failure from
    noise and just as bad: a channel that says nothing when the situation
    worsens.
    """
    fake_host.set_status(_status(warnings=[HEADROOM_WARNING]))
    run_alert_check()

    worse = dict(HEADROOM_WARNING, summary="artifact storage has 1,024 bytes left.")
    fake_host.set_status(_status(warnings=[worse]))
    run_alert_check()

    pushes = _pushes(fake_host, "TCKDB warning")
    assert len(pushes) == 2
    assert "1,024 bytes left" in pushes[1].body


def test_a_cleared_warning_is_recorded_without_a_push(fake_host, run_alert_check):
    """Good news that nobody was waiting for is noise.

    Recovery from a *verdict* is pushed, because you were left wondering
    whether it was still broken. Nothing was broken here. The state is
    still advanced, so the same warning returning later is an edge again --
    the failure mode being avoided is a warning that fires once and then
    never again.
    """
    fake_host.set_status(_status(warnings=[HEADROOM_WARNING]))
    run_alert_check()
    assert len(_pushes(fake_host, "TCKDB warning")) == 1

    fake_host.set_status(_status(warnings=[]))
    proc = run_alert_check()
    assert proc.returncode == 0
    assert len(_pushes(fake_host, "TCKDB warning")) == 1, "clearing pushed"
    assert "warning cleared" in proc.stdout

    fake_host.set_status(_status(warnings=[HEADROOM_WARNING]))
    run_alert_check()
    assert len(_pushes(fake_host, "TCKDB warning")) == 2, (
        "the warning returned and was swallowed, so the state never cleared"
    )


def test_a_warning_and_a_real_fault_are_both_delivered_at_their_own_priority(
    fake_host, run_alert_check
):
    """They are independent, and the degraded push must not be softened.

    The risk in adding a quiet channel is that the loud one gets quieter by
    accident -- a shared code path, a variable reused. Both are asserted in
    one run: high for the fault, low for the warning, exit 1 for the fault.
    """
    fake_host.set_status(
        _status(warnings=[HEADROOM_WARNING], degraded=["artifact_storage"])
    )
    proc = run_alert_check()

    assert proc.returncode == 1
    assert _priorities(fake_host, "TCKDB degraded") == ["high"]
    assert _priorities(fake_host, "TCKDB warning") == ["low"]


def test_an_unreachable_deployment_does_not_invent_a_warning(
    fake_host, run_alert_check
):
    """No body, no parse, no warning.

    ``warnings`` is set on the 200 path only. Under ``set -u`` an
    undeclared variable would kill the script before its verdict, which the
    exit-code contract turns into a 2 -- "the checker died" -- and would
    replace an accurate "unreachable" page with a misleading one.
    """
    proc = run_alert_check(
        env_overrides={"TCKDB_STATUS_URL": "http://127.0.0.1:1/status"}
    )

    assert proc.returncode == 1, (
        f"the checker did not reach a verdict: {proc.stderr}"
    )
    assert "unreachable" in proc.stdout
    assert _pushes(fake_host, "TCKDB warning") == []


def test_a_missing_status_endpoint_does_not_invent_a_warning(
    fake_host, run_alert_check
):
    """Same guard on the non-200 path, which is a separate branch."""
    fake_host.set_status({"detail": "Not Found"}, code=404)
    proc = run_alert_check()

    assert proc.returncode == 1
    assert "bad_endpoint" in proc.stdout
    assert _pushes(fake_host, "TCKDB warning") == []
