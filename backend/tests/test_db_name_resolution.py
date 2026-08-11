"""Unit tests for the test-database name resolution helper in conftest.

The contract ``_resolve_test_db_name`` has to satisfy has three parts, and
they are easy to satisfy two out of three:

* unique **per worker**, so ``-n 8`` gives eight databases rather than eight
  workers racing to drop and recreate one;
* unique **per run**, so two pytest processes on one host do not share
  databases and destroy each other mid-run;
* stable **within** a run, so a subprocess-based test inherits the same
  database the fixtures created.

Until 2026-08-10 only the first held. Names derived from
``PYTEST_XDIST_WORKER`` alone were identical across runs, and two agents in
two worktrees shared eight databases: one reported 2305 errors on a tree
that passed alone.

See ``docs/testing.md`` for the naming contract this pins.
"""

from __future__ import annotations

import os

import conftest
import pytest
from conftest import _resolve_test_db_name


@pytest.fixture
def _clean_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure neither DB_TEST_NAME nor PYTEST_XDIST_WORKER leak in from
    the surrounding session — the session ``db_engine`` fixture exports
    ``DB_TEST_NAME`` once resolved."""
    monkeypatch.delenv("DB_TEST_NAME", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


def test_explicit_db_test_name_is_a_label_not_the_whole_name(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    """An explicit name still carries the run token.

    It has to. The collision that actually happened was not two runs falling
    back to the same default — it was two runs *setting the same explicit
    name*, because that is what the gate command in the docs tells you to
    pass. A fix that only made the default unique would have left the real
    case untouched.
    """
    monkeypatch.setenv("DB_TEST_NAME", "tckdb_test_ci_job_42")

    resolved = _resolve_test_db_name()

    assert resolved.startswith("tckdb_test_ci_job_42_")
    assert resolved.endswith(conftest.RUN_TOKEN)
    assert resolved != "tckdb_test_ci_job_42"


def test_two_runs_never_share_a_database(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    monkeypatch.setenv("DB_TEST_NAME", "tckdb_test_shared")

    monkeypatch.setattr(conftest, "RUN_TOKEN", "1111aaaa")
    first = _resolve_test_db_name()
    monkeypatch.setattr(conftest, "RUN_TOKEN", "2222bbbb")
    second = _resolve_test_db_name()

    assert first != second


def test_explicit_db_test_name_is_still_per_worker_under_xdist(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    """An explicit name must not collapse every worker onto one database.

    Every gate script and CI job sets ``DB_TEST_NAME``. When the explicit name
    won unconditionally, ``-n auto`` had all workers drop, recreate and then
    write the same database concurrently.
    """
    monkeypatch.setenv("DB_TEST_NAME", "tckdb_test_api_ci")
    monkeypatch.setattr(conftest, "RUN_TOKEN", "1111aaaa")

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    first = _resolve_test_db_name()
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    second = _resolve_test_db_name()

    assert first == "tckdb_test_api_ci_1111aaaa_gw0"
    assert second == "tckdb_test_api_ci_1111aaaa_gw1"
    assert first != second


def test_long_explicit_name_keeps_workers_and_runs_distinct(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    """Postgres truncates over-long identifiers silently rather than failing.

    A 63-byte cap applied to the concatenation would give ``gw10`` and ``gw11``
    the same database, and would eat the run token first — turning the
    cross-run guarantee off for exactly the operators who pin long,
    descriptive job names. The base is trimmed so the whole suffix survives.
    """
    monkeypatch.setenv("DB_TEST_NAME", "tckdb_test_" + "x" * 80)
    monkeypatch.setattr(conftest, "RUN_TOKEN", "1111aaaa")

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw10")
    first = _resolve_test_db_name()
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw11")
    second = _resolve_test_db_name()

    assert len(first) <= 63
    assert len(second) <= 63
    assert first.endswith("_1111aaaa_gw10")
    assert second.endswith("_1111aaaa_gw11")
    assert first != second

    monkeypatch.setattr(conftest, "RUN_TOKEN", "2222bbbb")
    other_run = _resolve_test_db_name()
    assert other_run.endswith("_2222bbbb_gw11")
    assert other_run != second


def test_xdist_worker_derives_suffix(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    monkeypatch.setattr(conftest, "RUN_TOKEN", "1111aaaa")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")

    assert _resolve_test_db_name() == "tckdb_test_1111aaaa_gw0"


def test_xdist_worker_name_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    # Hypothetical pathological worker id with characters Postgres would
    # reject in an unquoted identifier — the helper must replace them.
    monkeypatch.setattr(conftest, "RUN_TOKEN", "1111aaaa")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw-1.master")

    resolved = _resolve_test_db_name()

    assert resolved.startswith("tckdb_test_")
    assert "-" not in resolved
    assert "." not in resolved
    assert resolved == "tckdb_test_1111aaaa_gw_1_master"


def test_fallback_without_xdist_is_still_run_unique(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    monkeypatch.setattr(conftest, "RUN_TOKEN", "1111aaaa")

    assert _resolve_test_db_name() == "tckdb_test_1111aaaa"


def test_resolution_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    """Resolving the exported name again must not stack a second token.

    ``db_engine`` writes the resolved name back into ``DB_TEST_NAME`` so
    subprocess tests inherit it; anything that re-resolves in the same
    process — a fixture driven directly, a nested harness — would otherwise
    end up naming a database that does not exist.
    """
    monkeypatch.setenv("DB_TEST_NAME", "tckdb_test_base")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw2")

    once = _resolve_test_db_name()
    monkeypatch.setenv("DB_TEST_NAME", once)

    assert _resolve_test_db_name() == once


def test_run_token_is_shared_through_the_environment() -> None:
    """Workers inherit the controller's token rather than minting their own.

    ``os.environ.setdefault`` at conftest import is what does it; the
    assertion is here so a refactor that drops the export is noticed. A
    per-worker token would still be *correct* (uniqueness is only ever
    required between runs), but the databases of one run would no longer be
    identifiable as one run.
    """
    assert os.environ.get("TCKDB_TEST_RUN_TOKEN") == conftest.RUN_TOKEN
    assert conftest.RUN_TOKEN


def test_session_fixture_exports_db_test_name(db_engine) -> None:
    """The session-scoped ``db_engine`` fixture must export the resolved
    name back into ``os.environ`` so subprocess tests (e.g. the bundle
    export CLI smoke test) inherit the same database."""
    assert "DB_TEST_NAME" in os.environ
    assert os.environ["DB_TEST_NAME"] == db_engine.url.database
