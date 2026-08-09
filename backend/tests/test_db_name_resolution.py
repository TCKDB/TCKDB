"""Unit tests for the test-database name resolution helper in conftest.

Covers the three branches of ``_resolve_test_db_name`` so the parallel-safe
naming contract documented in ``docs/testing.md`` is enforced.
"""

from __future__ import annotations

import os

import pytest
from conftest import _resolve_test_db_name


@pytest.fixture
def _clean_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure neither DB_TEST_NAME nor PYTEST_XDIST_WORKER leak in from
    the surrounding session — the session ``db_engine`` fixture exports
    ``DB_TEST_NAME`` once resolved."""
    monkeypatch.delenv("DB_TEST_NAME", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


def test_explicit_db_test_name_used_verbatim_without_xdist(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    monkeypatch.setenv("DB_TEST_NAME", "ci_job_42_db")

    assert _resolve_test_db_name() == "ci_job_42_db"


def test_explicit_db_test_name_is_still_per_worker_under_xdist(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    """An explicit name must not collapse every worker onto one database.

    Every gate script and CI job sets ``DB_TEST_NAME``. When the explicit name
    won unconditionally, ``-n auto`` had all workers drop, recreate and then
    write the same database concurrently.
    """
    monkeypatch.setenv("DB_TEST_NAME", "tckdb_test_api_ci")

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    first = _resolve_test_db_name()
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    second = _resolve_test_db_name()

    assert first == "tckdb_test_api_ci_gw0"
    assert second == "tckdb_test_api_ci_gw1"
    assert first != second


def test_long_explicit_name_keeps_workers_distinct(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    """Postgres truncates over-long identifiers silently rather than failing.

    A 63-byte cap applied to the concatenation would give ``gw10`` and ``gw11``
    the same database. The base is trimmed so the worker suffix survives.
    """
    monkeypatch.setenv("DB_TEST_NAME", "tckdb_test_" + "x" * 80)

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw10")
    first = _resolve_test_db_name()
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw11")
    second = _resolve_test_db_name()

    assert len(first) <= 63
    assert len(second) <= 63
    assert first.endswith("_gw10")
    assert second.endswith("_gw11")
    assert first != second


def test_xdist_worker_derives_suffix(monkeypatch: pytest.MonkeyPatch, _clean_db_env: None) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")

    assert _resolve_test_db_name() == "tckdb_test_gw0"


def test_xdist_worker_name_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, _clean_db_env: None
) -> None:
    # Hypothetical pathological worker id with characters Postgres would
    # reject in an unquoted identifier — the helper must replace them.
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw-1.master")

    resolved = _resolve_test_db_name()

    assert resolved.startswith("tckdb_test_")
    assert "-" not in resolved
    assert "." not in resolved
    assert resolved == "tckdb_test_gw_1_master"


def test_fallback_uses_pid(monkeypatch: pytest.MonkeyPatch, _clean_db_env: None) -> None:
    resolved = _resolve_test_db_name()

    assert resolved == f"tckdb_test_{os.getpid()}"


def test_session_fixture_exports_db_test_name(db_engine) -> None:
    """The session-scoped ``db_engine`` fixture must export the resolved
    name back into ``os.environ`` so subprocess tests (e.g. the bundle
    export CLI smoke test) inherit the same database."""
    assert "DB_TEST_NAME" in os.environ
    assert os.environ["DB_TEST_NAME"]
