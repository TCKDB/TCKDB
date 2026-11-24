"""Shared fixtures for the machine-review job route tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.app import create_app
from app.api.config import settings as app_settings
from app.api.deps import get_db, get_write_db
from app.db.models.common import SubmissionKind
from app.db.models.machine_review_job import MachineReviewJob
from app.services.submission import create_submission

JOBS = "/api/v1/machine-review/jobs"
PROVIDERS = "/api/v1/machine-review/providers"
SETTINGS = "/api/v1/admin/machine-review/settings"


@pytest.fixture
def fake_mode(monkeypatch):
    """Pin the deployment to the scripted provider on a local deployment."""
    monkeypatch.setattr(app_settings, "ai_review_assistant_mode", "test")
    monkeypatch.setattr(app_settings, "deployment_mode", "local")


@pytest.fixture
def anon_client(db_session):
    """A client with the DB overridden but real authentication."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


@pytest.fixture
def submission(db_session, _api_test_user):
    row = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.thermo,
        title="job routes",
        summary="compact",
    )
    db_session.flush()
    return row


def job_count(db_session, submission_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(MachineReviewJob)
    if submission_id is not None:
        stmt = stmt.where(MachineReviewJob.submission_id == submission_id)
    return db_session.scalar(stmt)


def keys_anywhere(value) -> set[str]:
    """Every dict key at any depth."""
    if isinstance(value, dict):
        out = set(value)
        for v in value.values():
            out |= keys_anywhere(v)
        return out
    if isinstance(value, list):
        out: set[str] = set()
        for v in value:
            out |= keys_anywhere(v)
        return out
    return set()
