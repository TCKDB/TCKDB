"""A second pytest run must be named, not discovered by inference.

Four agents each running an 8-way ``-n`` gate against one PostgreSQL with
``max_connections = 100`` exhaust the pool. What that looks like from inside a
run is 21 failures and 243 errors reading ``connection is bad`` and ``sorry,
too many clients already``, spread across whichever tests happened to be
executing -- which is indistinguishable from a real regression. It has twice
been read as one, and one of those times a false baseline was nearly published.

Run-unique database names (#126) removed the *name* collision and left the
*connection* collision with nothing to name it. These tests cover the part of
``conftest`` that names it: the attribution of harness databases to the runs
that stamped them, and the refusal that fires before a doomed session creates
anything.

The condition itself cannot be arranged on demand -- it needs a second real
pytest run at a particular moment -- so the query is separated from the
arithmetic and the arithmetic is what is asserted here. ``_read_server_load``
against a live server is exercised by every session that runs this file.
"""

from __future__ import annotations

import os
import subprocess
import sys

import conftest
import pytest

HOST = conftest._safe_host()
OTHER = "b0b0b0b0"


def marker(token: str, pid: int | None = None, host: str | None = None) -> str:
    return (
        f"{conftest._MARKER_PREFIX} host={host or HOST} "
        f"pid={pid if pid is not None else os.getpid()} run={token}"
    )


def load(rows, max_connections: int = 100, in_use: int = 10):
    return conftest._attribute_databases(rows, max_connections, in_use)


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


def test_another_live_run_is_attributed_to_its_token() -> None:
    result = load(
        [
            ("tckdb_test_a_1234_gw0", marker(OTHER), 4),
            ("tckdb_test_a_1234_gw1", marker(OTHER), 5),
        ]
    )

    assert len(result.foreign_runs) == 1
    run = result.foreign_runs[0]
    assert run.token == OTHER
    assert run.backends == 9
    assert sorted(run.databases) == ["tckdb_test_a_1234_gw0", "tckdb_test_a_1234_gw1"]
    assert OTHER in run.describe()
    assert "9 connection(s)" in run.describe()


def test_this_runs_own_databases_are_not_reported_as_competition() -> None:
    """Otherwise every session would accuse itself and the banner is noise."""
    result = load([("tckdb_test_mine_gw0", marker(conftest.RUN_TOKEN), 4)])
    assert result.foreign_runs == []


def test_a_dead_runs_leftovers_are_not_competition() -> None:
    """An abandoned database holds no connections; the sweep deals with it.

    Reporting it would make the banner permanent on any host that has ever
    had a run killed, and a permanent banner is one nobody reads.
    """
    result = load([("tckdb_test_dead_gw0", marker(OTHER, pid=2**22 - 1), 0)])
    assert result.foreign_runs == []


def test_a_run_on_another_host_is_reported_even_though_its_pid_is_unreadable() -> None:
    """Two machines can share one server, and the pid test means nothing then.

    The conservative answer for *reclaiming* is to leave a foreign host alone;
    the conservative answer for *reporting* is the opposite, because those
    connections are real whatever this host can see.
    """
    result = load([("tckdb_test_remote_gw0", marker(OTHER, pid=1, host="elsewhere"), 6)])
    assert [run.token for run in result.foreign_runs] == [OTHER]
    assert result.foreign_runs[0].host == "elsewhere"


def test_unmarked_harness_databases_are_counted_but_never_attributed() -> None:
    result = load(
        [
            ("tckdb_test_orphan", None, 1),
            ("tckdb_test_hand_made", "something else entirely", 0),
        ]
    )
    assert result.foreign_runs == []
    assert result.unattributed == 2


def test_two_other_runs_are_reported_separately() -> None:
    result = load(
        [
            ("tckdb_test_a_gw0", marker("aaaaaaaa"), 3),
            ("tckdb_test_b_gw0", marker("cccccccc"), 4),
            ("tckdb_test_b_gw1", marker("cccccccc"), 4),
        ]
    )
    assert [run.token for run in result.foreign_runs] == ["aaaaaaaa", "cccccccc"]
    assert [run.backends for run in result.foreign_runs] == [3, 8]


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------


def test_a_server_with_room_is_not_refused() -> None:
    assert conftest._concurrency_refusal(load([], 200, 20), required=46) is None


def test_a_full_server_is_refused_before_anything_is_created() -> None:
    result = load([("tckdb_test_a_gw0", marker(OTHER), 35)], max_connections=100, in_use=70)

    refusal = conftest._concurrency_refusal(result, required=46)

    assert refusal is not None
    assert "does not have room" in refusal
    assert "max_connections=100" in refusal
    assert "70 client backend(s) in use, 30 free" in refusal
    # The message must name the competition, or the reader is back to guessing.
    assert OTHER in refusal
    # ...and it must say what to do about it.
    assert "TCKDB_TEST_WORKERS" in refusal
    assert "max_connections" in refusal
    assert conftest._CONCURRENCY_CHECK_ENV in refusal


def test_the_refusal_fires_even_with_no_identifiable_foreign_run() -> None:
    """Capacity is capacity.

    The other consumer may be a dev server, a psql, or a run started before
    markers existed. A check that only fired on an *identified* pytest run
    would miss the most common shape of a busy workstation.
    """
    refusal = conftest._concurrency_refusal(load([], 100, 95), required=46)
    assert refusal is not None
    assert "5 free" in refusal


def test_the_requirement_scales_with_the_worker_count(monkeypatch) -> None:
    monkeypatch.delenv(conftest._MIN_HEADROOM_ENV, raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT", raising=False)
    monkeypatch.setattr(conftest, "_RESOLVED_WORKERS", None)

    monkeypatch.setenv("TCKDB_TEST_WORKERS", "1")
    one = conftest._required_backends()
    monkeypatch.setenv("TCKDB_TEST_WORKERS", "8")
    eight = conftest._required_backends()

    assert eight > one
    assert eight == 8 * conftest._BACKENDS_PER_WORKER + conftest._BACKENDS_PER_RUN


def test_the_requirement_can_be_overridden_to_exercise_the_refusal(monkeypatch) -> None:
    """The knob the live proof of this check uses.

    Arranging a genuinely full server on demand means starting three more
    pytest runs. Raising the requirement instead reaches the same code path
    with the same message, which is what makes the refusal something anyone
    can see fire rather than something they have to believe.
    """
    monkeypatch.setenv(conftest._MIN_HEADROOM_ENV, "100000")
    assert conftest._required_backends() == 100000


def test_the_whole_check_can_be_switched_off(monkeypatch) -> None:
    """A check with no off switch is a check somebody deletes."""
    monkeypatch.setenv(conftest._CONCURRENCY_CHECK_ENV, "0")
    assert conftest._sample_server_load() is None


def test_the_refusal_reaches_the_terminal_as_one_readable_message() -> None:
    """The refusal must not arrive as an ``INTERNALERROR>`` traceback.

    An exception raised from ``pytest_sessionstart`` is rendered by pluggy as
    a wall of frames with the message at the bottom, which reads as "pytest
    broke" -- swapping one illegible failure for another and buying nothing.
    ``ConcurrentTestRunError`` derives from ``pytest.UsageError`` so pytest's
    own entry point prints it as ``ERROR:`` and exits 4, and that is a
    property of pytest's plumbing rather than of this repository, so it is
    checked rather than assumed.

    Driven through the requirement override, because the alternative is
    starting three more pytest runs to fill a real server.
    """
    env = dict(os.environ)
    # A nested pytest inside an xdist worker inherits this and would take the
    # worker early-return, checking nothing.
    env.pop("PYTEST_XDIST_WORKER", None)
    env.pop("PYTEST_XDIST_WORKER_COUNT", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env[conftest._MIN_HEADROOM_ENV] = "100000"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "--collect-only", "-q",
         "tests/test_concurrent_run_detection.py"],
        cwd=conftest.REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = result.stdout + result.stderr

    assert "INTERNALERROR" not in combined, (
        "the refusal arrives as an internal error with a pluggy traceback:\n"
        + combined
    )
    assert result.returncode == 4, f"expected pytest's usage-error exit; got {result.returncode}"
    assert "does not have room for this test run" in combined
    assert "Do one of:" in combined


def test_the_check_lets_a_normal_session_through() -> None:
    """Guard the guard: a refusal that always fired would pass the test above.

    Same subprocess, same everything, without the requirement override.
    """
    env = dict(os.environ)
    env.pop("PYTEST_XDIST_WORKER", None)
    env.pop("PYTEST_XDIST_WORKER_COUNT", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop(conftest._MIN_HEADROOM_ENV, None)
    env["TCKDB_TEST_WORKERS"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "--collect-only", "-q",
         "tests/test_concurrent_run_detection.py"],
        cwd=conftest.REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# The live query
# ---------------------------------------------------------------------------


def test_the_snapshot_reads_a_real_server(db_engine) -> None:
    """Guard the guard: the arithmetic above is only worth anything if the
    query feeding it works against the PostgreSQL this suite actually uses.

    Takes ``db_engine`` so it runs against the session's own server, and
    asserts the two numbers that decide everything: a plausible
    ``max_connections`` and a client-backend count that excludes the
    checkpointer, the walwriter and the autovacuum launcher -- background
    processes that appear in ``pg_stat_activity`` and occupy no connection
    slot. Counting them overstates the load by five on an idle server.
    """
    result = conftest._sample_server_load()
    if result is None:  # pragma: no cover - only when the check is disabled
        pytest.skip("concurrency check disabled in this environment")

    assert result.max_connections >= 10
    assert 0 < result.in_use <= result.max_connections
    assert result.headroom == result.max_connections - result.in_use

    # This session owns at least one harness database and must not have been
    # attributed to a foreign run.
    assert conftest.RUN_TOKEN not in {run.token for run in result.foreign_runs}
