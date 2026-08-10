"""The checker decides on ``degraded``, not only on ``status``.

``/status`` derives both fields from the same set of unhealthy
components, so reading one works — today. It would go on appearing to
work, silently, the moment that stops being true: a consumer that
branches on a summary field while the components underneath it say
otherwise reports an outage as healthy, which is this deployment's
existing failure mode rather than a hypothetical one.

The ``jq``-free path is exercised alongside every case, because it is a
separately written parser and the one that has been wrong before: a
string-shaped grep against ``"degraded": [...]`` returns empty for every
value including a non-empty one, i.e. it reports a degraded deployment
as clean.
"""

from __future__ import annotations

import pytest

CONTRADICTORY = {
    # status says ok; the components say otherwise. A derivation bug, and
    # precisely the case a single-field consumer waves through.
    "status": "ok",
    "degraded": ["worker"],
    "components": {
        "database": {"healthy": True, "alembic_revision": "abc123", "reason": None},
        "worker": {"healthy": False, "reason": "inline worker thread is not running"},
        "artifact_storage": {"healthy": True, "reachable": True, "reason": None},
    },
}

DEGRADED = {
    "status": "degraded",
    "degraded": ["artifact_storage", "worker"],
    "components": {
        "database": {"healthy": True, "alembic_revision": "abc123", "reason": None},
        "worker": {"healthy": False, "reason": "inline worker thread is not running"},
        "artifact_storage": {
            "healthy": False,
            "reachable": False,
            "reason": "cannot reach object store",
        },
    },
}

HEALTHY = {
    "status": "ok",
    "degraded": [],
    "components": {
        "database": {"healthy": True, "alembic_revision": "abc123", "reason": None},
        "worker": {"healthy": True, "reason": None},
        "artifact_storage": {"healthy": True, "reachable": True, "reason": None},
    },
}


@pytest.mark.parametrize("drop_jq", [False, True], ids=["with-jq", "without-jq"])
def test_a_non_empty_degraded_list_alerts_even_when_status_says_ok(
    fake_host, run_alert_check, drop_jq
):
    fake_host.set_status(CONTRADICTORY)
    proc = run_alert_check(drop_jq=drop_jq)

    assert proc.returncode == 1, "a listed degraded component is not a healthy deployment"
    assert "status=degraded" in proc.stdout
    pushes = fake_host.paths("/ntfy")
    assert len(pushes) >= 1
    alert = next(p for p in pushes if "degraded" in p.headers.get("title", "").lower())
    assert "contradict" in alert.body or "status=ok while listing" in alert.body, (
        "the push should name the self-contradiction, which has a different "
        "fix from a component simply being down"
    )
    assert "worker" in alert.body


@pytest.mark.parametrize("drop_jq", [False, True], ids=["with-jq", "without-jq"])
def test_a_degraded_deployment_is_detected_on_both_parse_paths(
    fake_host, run_alert_check, drop_jq
):
    fake_host.set_status(DEGRADED)
    proc = run_alert_check(drop_jq=drop_jq)

    assert proc.returncode == 1
    assert "status=degraded" in proc.stdout
    alert = next(
        p for p in fake_host.paths("/ntfy")
        if "degraded" in p.headers.get("title", "").lower()
    )
    assert "artifact_storage" in alert.body, (
        "the fallback parser must read the degraded ARRAY, not a string"
    )
    assert "worker" in alert.body


@pytest.mark.parametrize("drop_jq", [False, True], ids=["with-jq", "without-jq"])
def test_a_healthy_deployment_stays_healthy_on_both_parse_paths(
    fake_host, run_alert_check, drop_jq
):
    """The fallback must not manufacture a degraded verdict from an empty list.

    The old fallback assigned a literal placeholder to ``degraded``; once
    that field decides anything, a placeholder is a permanent false alarm,
    and a checker that cries wolf every five minutes is one you turn off.
    """
    fake_host.set_status(HEALTHY)
    proc = run_alert_check(drop_jq=drop_jq)

    assert proc.returncode == 0
    assert "status=ok" in proc.stdout
    assert fake_host.paths("/ntfy") == [] or all(
        "dead man" in p.headers.get("title", "") for p in fake_host.paths("/ntfy")
    ), "a healthy first run stays quiet apart from the one-off deadman notice"
