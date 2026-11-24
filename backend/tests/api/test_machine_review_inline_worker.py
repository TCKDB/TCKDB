"""The API process starts the machine-review worker thread beside the upload worker."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.config import settings as app_settings


@pytest.fixture
def started(monkeypatch):
    """Counters in place of both worker starters, so no real thread runs in the suite."""
    calls = {"upload": 0, "review": 0}
    monkeypatch.setenv("TCKDB_INLINE_WORKER", "true")
    monkeypatch.setenv("TCKDB_STARTUP_PROBES", "false")
    monkeypatch.setattr(
        "app.workers.upload_worker.run_worker_thread", lambda *a, **k: calls.__setitem__("upload", calls["upload"] + 1)
    )
    monkeypatch.setattr(
        "app.workers.machine_review_worker.run_worker_thread",
        lambda *a, **k: calls.__setitem__("review", calls["review"] + 1),
    )
    return calls


@pytest.mark.parametrize("mode,expected", [("test", 1), ("off", 0)])
def test_review_worker_starts_unless_the_mode_is_off(started, monkeypatch, mode, expected):
    monkeypatch.setattr(app_settings, "ai_review_assistant_mode", mode)
    with TestClient(create_app()):
        pass
    assert started == {"upload": 1, "review": expected}


def test_no_inline_worker_starts_nothing(started, monkeypatch):
    monkeypatch.setenv("TCKDB_INLINE_WORKER", "false")
    monkeypatch.setattr(app_settings, "ai_review_assistant_mode", "test")
    with TestClient(create_app()):
        pass
    assert started == {"upload": 0, "review": 0}
