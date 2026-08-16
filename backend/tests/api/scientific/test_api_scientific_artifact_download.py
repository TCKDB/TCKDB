"""API tests for approved, integrity-verified artifact downloads."""

from __future__ import annotations

import hashlib
import logging

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.calculation import ArtifactIntegrityEvent
from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
)
from tests.services.test_artifact_integrity import _SessionProxy

#: Where the explanatory log line for a download 502/503 is now emitted.
#:
#: It used to be the route: the route caught the typed exception and
#: re-raised a bare ``HTTPException``, which bypassed both the handler in
#: ``app.api.errors`` *and* that handler's logging, so the route had to log
#: for itself or the 2026-08-05 storage outage would have been a bare
#: access-log 5xx again. #212 made the route re-raise the exception
#: unchanged — which is what lets the handler mint
#: ``artifact_integrity_failed`` / ``artifact_storage_unavailable`` instead
#: of ``http_502`` / ``http_503`` — and the handler logs the same
#: exception, with ``exc_info`` and with the request's method and path.
#:
#: So the *claim* the two tests below make is unchanged and still asserted:
#: an operator can read the reason and a traceback out of the journal. Only
#: the logger name moved. Keeping a duplicate log in the route would mean
#: two records of one event.
_ERROR_HANDLER_LOGGER = "app.api.errors"
from tests.api.scientific.test_api_scientific_artifacts import (
    _make_species_owned_calc,
)
from tests.services.scientific_read._factories import attach_artifact, set_review


def _events_for(db_session, sha256):
    return list(
        db_session.scalars(
            select(ArtifactIntegrityEvent)
            .where(ArtifactIntegrityEvent.sha256 == sha256)
            .order_by(ArtifactIntegrityEvent.id)
        ).all()
    )


def _record_into(monkeypatch, db_session):
    """Point the recorder's independent transaction at the test session.

    The recorder opens its own session on purpose — the durability of
    that choice is asserted in ``tests/services/test_artifact_integrity``
    against a real engine. Here the subject is what the *route* records,
    so the transaction boundary is neutered and the per-test rollback
    still owns cleanup.
    """
    monkeypatch.setattr(
        "app.services.artifact_integrity.head_artifact_object",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("app.api.deps.SessionLocal", _SessionProxy(db_session))


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
        raise ArtifactIntegrityError(
            "corrupt",
            finding=ArtifactIntegrityFinding.digest_mismatch,
            sha256=artifact.sha256,
            observed_sha256="1" * 64,
        )

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    _record_into(monkeypatch, db_session)
    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 502
    body = response.json()
    # The code, not the prose. The prose used to be the route's own
    # sentence, because the route raised its own ``HTTPException``; that
    # is exactly what made this route answer ``http_502`` where the
    # upload path answered ``artifact_integrity_failed`` for the same
    # break (#212). Asserting the code is what would notice if the route
    # started minting its own answer again — asserting a sentence would
    # not.
    assert body["code"] == "artifact_integrity_failed", body
    assert body["detail"] == (
        "A stored artifact failed integrity verification. This has "
        "been recorded; retrying will not clear it."
    )


def test_artifact_download_maps_storage_outage_to_503(
    client, db_session, monkeypatch, caplog
) -> None:
    """A storage outage on the download path is a 503 — and is logged.

    Without an explanatory log line, a storage outage appears in the
    journal as a bare access-log 503 naming a subsystem with no record of
    why — the exact thing that made the 2026-08-05 outage take so long to
    diagnose. That claim is unchanged; where it is satisfied has moved.

    The route used to convert the exception to an ``HTTPException``,
    which bypassed the ``ArtifactStorageUnavailable`` handler in
    ``app.api.errors`` *and* its logging, so the route logged for itself.
    It also meant the download answered ``http_503`` where the rest of
    the API answers ``artifact_storage_unavailable``. The route now
    re-raises the exception unchanged, so the handler both logs it and
    names it — see ``_ERROR_HANDLER_LOGGER``. The assertions below are
    the same assertions against that logger, plus the code.
    """
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )
    caplog.set_level(logging.WARNING, logger=_ERROR_HANDLER_LOGGER)

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
    assert response.json()["code"] == "artifact_storage_unavailable", response.text

    records = [rec for rec in caplog.records if rec.name == _ERROR_HANDLER_LOGGER]
    assert records, "storage 503 on download produced no explanatory log record"
    # The reason, not just the verdict: the endpoint it could not reach.
    assert "EndpointConnectionError" in records[0].getMessage()
    assert "127.0.0.1:9000" in records[0].getMessage()
    assert records[0].exc_info is not None
    # And the request it happened on, which the route's own log line never
    # carried: the handler is given the Request object, the route is not.
    assert "/download" in records[0].getMessage()


def test_artifact_download_logs_why_integrity_verification_failed(
    client, db_session, monkeypatch, caplog
) -> None:
    """A 502 here means stored bytes no longer match their digest.

    That is data corruption, and the public body says nothing about which
    object or why. If it is not in the log it is nowhere. Emitted by the
    handler rather than the route since #212 — see
    ``_ERROR_HANDLER_LOGGER`` — because the route now re-raises the typed
    exception instead of replacing it.
    """
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )
    caplog.set_level(logging.ERROR, logger=_ERROR_HANDLER_LOGGER)

    def fake_load(*_args, **_kwargs):
        raise ArtifactIntegrityError(
            "retrieved sha=deadbeef",
            finding=ArtifactIntegrityFinding.digest_mismatch,
            sha256=artifact.sha256,
            observed_sha256="2" * 64,
        )

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    _record_into(monkeypatch, db_session)
    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 502
    assert response.json()["code"] == "artifact_integrity_failed", response.text
    records = [rec for rec in caplog.records if rec.name == _ERROR_HANDLER_LOGGER]
    assert records, "integrity 502 produced no explanatory log record"
    assert "retrieved sha=deadbeef" in records[0].getMessage()
    assert records[0].exc_info is not None


def test_integrity_502_writes_a_durable_record_not_just_a_log(
    client, db_session, monkeypatch
) -> None:
    """A log line nobody greps is not a record.

    This reader gets a 502 either way. The row is what makes the break
    visible to the *next* reader and to the trust evaluator, without
    anyone having to go looking in the journal. ADR 0014.
    """
    artifact, content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )
    observed = hashlib.sha256(b"swapped").hexdigest()

    def fake_load(*_args, **_kwargs):
        raise ArtifactIntegrityError(
            f"Artifact digest verification failed for sha={artifact.sha256}: "
            f"retrieved sha={observed}.",
            finding=ArtifactIntegrityFinding.digest_mismatch,
            sha256=artifact.sha256,
            observed_sha256=observed,
            expected_bytes=len(content),
            observed_bytes=7,
        )

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    _record_into(monkeypatch, db_session)

    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 502
    events = _events_for(db_session, artifact.sha256)
    assert len(events) == 1, "the 502 recorded nothing durable"
    event = events[0]
    assert event.finding is ArtifactIntegrityFinding.digest_mismatch
    assert event.detected_during is ArtifactIntegrityDetectionContext.download
    assert event.observed_sha256 == observed
    assert event.expected_bytes == len(content)
    assert event.artifact_id == artifact.id
    # Who hit it — the question "has this ever happened, and to whom" is
    # unanswerable without it.
    assert event.created_by is not None


def test_store_reporting_no_such_key_is_recorded_as_a_missing_object(
    client, db_session, monkeypatch
) -> None:
    """A row referencing an object the store says is gone is a custody break."""
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )

    def fake_load(*_args, **_kwargs):
        raise ArtifactStorageUnavailable(
            f"Artifact storage read failed for sha={artifact.sha256}: "
            "ClientError: NoSuchKey",
            missing=True,
        )

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    _record_into(monkeypatch, db_session)

    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 503
    (event,) = _events_for(db_session, artifact.sha256)
    assert event.finding is ArtifactIntegrityFinding.object_missing
    assert event.observed_sha256 is None


def test_unreachable_store_records_nothing(client, db_session, monkeypatch) -> None:
    """An endpoint that never answered says nothing about the object.

    Same 503 to the client as the case above, and the opposite fact for
    custody: recording a break here would manufacture a hard fail out of
    a network blip.
    """
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )

    def fake_load(*_args, **_kwargs):
        raise ArtifactStorageUnavailable(
            "Artifact storage read failed: EndpointConnectionError"
        )

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    _record_into(monkeypatch, db_session)

    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 503
    assert _events_for(db_session, artifact.sha256) == []


def test_a_failing_recorder_does_not_turn_the_502_into_a_500(
    client, db_session, monkeypatch
) -> None:
    """Recording is best effort; the reader's answer is not."""
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )

    def fake_load(*_args, **_kwargs):
        raise ArtifactIntegrityError(
            "corrupt",
            finding=ArtifactIntegrityFinding.digest_mismatch,
            sha256=artifact.sha256,
            observed_sha256="9" * 64,
        )

    def exploding_factory():
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    monkeypatch.setattr(
        "app.services.artifact_integrity.head_artifact_object", lambda *a, **k: None
    )
    monkeypatch.setattr("app.api.deps.SessionLocal", exploding_factory)

    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )
    assert response.status_code == 502


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
