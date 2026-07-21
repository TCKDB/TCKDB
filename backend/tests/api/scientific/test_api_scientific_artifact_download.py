"""API tests for approved, integrity-verified artifact downloads."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.services.artifact_storage import ArtifactIntegrityError
from tests.api.scientific.test_api_scientific_artifacts import (
    _make_species_owned_calc,
)
from tests.services.scientific_read._factories import attach_artifact, set_review


def _downloadable_artifact(db_session, *, status: RecordReviewStatus):
    content = b"curator-approved artifact bytes"
    sha256 = hashlib.sha256(content).hexdigest()
    _, _, calculation = _make_species_owned_calc(db_session)
    artifact = attach_artifact(db_session, calculation=calculation)
    artifact.sha256 = sha256
    artifact.bytes = len(content)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calculation.id,
        status=status,
    )
    db_session.flush()
    return artifact, content


def test_approved_artifact_download_returns_verified_bytes(
    client, db_session, monkeypatch
) -> None:
    artifact, content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )

    def fake_load(sha256: str, *, expected_bytes: int | None = None) -> bytes:
        assert sha256 == artifact.sha256
        assert expected_bytes == len(content)
        return content

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["x-content-sha256"] == artifact.sha256
    assert response.headers["etag"] == f'"{artifact.sha256}"'
    # Authenticated PII-bearing bytes must not be retained by shared caches.
    assert response.headers["cache-control"] == "private, no-store"


def test_nonapproved_artifact_download_is_indistinguishable_from_missing(
    client, db_session, monkeypatch
) -> None:
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.under_review
    )
    called = False

    def fake_load(*_args, **_kwargs):
        nonlocal called
        called = True
        return b"must not be returned"

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 404
    assert called is False


def test_artifact_download_maps_integrity_failure_to_502(
    client, db_session, monkeypatch
) -> None:
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )

    def fake_load(*_args, **_kwargs):
        raise ArtifactIntegrityError("corrupt")

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Stored artifact failed integrity verification."
    )


def test_artifact_download_rejects_malformed_digest(client, db_session) -> None:
    response = client.get("/api/v1/scientific/artifacts/not-a-digest/download")
    assert response.status_code == 422


def test_anonymous_artifact_download_returns_401(db_session, monkeypatch) -> None:
    """Raw approved bytes must never reach an unauthenticated caller.

    The default ``client`` fixture overrides ``get_current_user`` to a
    seeded test user, so it cannot exercise the anonymous path. Here we
    build an app WITHOUT that override: the request carries no API key or
    session cookie and must be rejected by the auth gate (401) before any
    byte load. FastAPI resolves the auth sub-dependency before the
    endpoint's own path-param validation, so an anonymous caller gets a
    uniform 401 for every input (even a malformed digest) — no 401-vs-404
    existence oracle. We use a well-formed but non-existent digest: the
    point is that we get 401, not 404, and never touch storage.
    """
    called = False

    def fake_load(*_args, **_kwargs):
        nonlocal called
        called = True
        return b"must not be returned"

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as anon:
        response = anon.get(f"/api/v1/scientific/artifacts/{'0' * 64}/download")

    assert response.status_code == 401
    assert called is False


def test_authenticated_user_can_download_approved_artifact(
    client, db_session, monkeypatch
) -> None:
    """A regular authenticated user (the default client actor) may pull
    approved bytes — the gate requires authentication, not a curator role."""
    artifact, content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )
    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes",
        lambda *_a, **_k: content,
    )
    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )
    assert response.status_code == 200
    assert response.content == content
