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


def test_explicit_db_test_name_wins(monkeypatch: pytest.MonkeyPatch, _clean_db_env: None) -> None:
    monkeypatch.setenv("DB_TEST_NAME", "ci_job_42_db")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")  # must be ignored

    assert _resolve_test_db_name() == "ci_job_42_db"


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
