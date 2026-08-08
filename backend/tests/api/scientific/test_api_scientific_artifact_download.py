"""API tests for approved, integrity-verified artifact downloads."""

from __future__ import annotations

import hashlib
import logging

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
)

_ROUTE_LOGGER = "app.api.routes.scientific.artifacts"
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


def test_artifact_download_maps_storage_outage_to_503(
    client, db_session, monkeypatch, caplog
) -> None:
    """A storage outage on the download path is a 503 — and is logged.

    This route converts the exception to ``HTTPException``, so it bypasses
    the ``ArtifactStorageUnavailable`` handler in ``app.api.errors`` and
    that handler's logging. Without a log line here, a storage outage
    appears in the journal as a bare access-log 503 naming a subsystem
    with no record of why — the exact thing that made the 2026-08-05
    outage take so long to diagnose.
    """
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )
    caplog.set_level(logging.WARNING, logger=_ROUTE_LOGGER)

    def fake_load(*_args, **_kwargs):
        raise ArtifactStorageUnavailable(
            "Artifact storage read failed: EndpointConnectionError: "
            'Could not connect to the endpoint URL: "http://127.0.0.1:9000"'
        )

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Artifact storage is unavailable."

    records = [rec for rec in caplog.records if rec.name == _ROUTE_LOGGER]
    assert records, "storage 503 on download produced no explanatory log record"
    # The reason, not just the verdict: the endpoint it could not reach.
    assert "EndpointConnectionError" in records[0].getMessage()
    assert "127.0.0.1:9000" in records[0].getMessage()
    assert records[0].exc_info is not None


def test_artifact_download_logs_why_integrity_verification_failed(
    client, db_session, monkeypatch, caplog
) -> None:
    """A 502 here means stored bytes no longer match their digest.

    That is data corruption, and the public body says nothing about which
    object or why. If it is not in the log it is nowhere.
    """
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )
    caplog.set_level(logging.ERROR, logger=_ROUTE_LOGGER)

    def fake_load(*_args, **_kwargs):
        raise ArtifactIntegrityError("retrieved sha=deadbeef")

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 502
    records = [rec for rec in caplog.records if rec.name == _ROUTE_LOGGER]
    assert records, "integrity 502 produced no explanatory log record"
    assert "retrieved sha=deadbeef" in records[0].getMessage()
    assert records[0].exc_info is not None


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
