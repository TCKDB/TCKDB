"""API tests for integrity-verified artifact downloads.

Two ways in, and they are tested as two: *approved* (a curator published
it, any authenticated caller may pull it) and *owned* (the caller
deposited it). The second was added 2026-08-24; before it, an
approval-only gate had never opened on the hosted instance — 563 of 563
artifacts hung off ``not_reviewed`` calculations — so the depositor could
not retrieve their own upload. What did *not* change is the auth gate:
ownership is a reason to serve an authenticated caller, never a reason to
serve an anonymous one.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.app_user import AppUser
from app.db.models.calculation import ArtifactIntegrityEvent
from app.db.models.common import (
    AppUserRole,
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
    RecordReviewStatus,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
)
from app.services.deposit_ownership import user_owns_calculation_deposit
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


def _downloadable_artifact(
    db_session,
    *,
    status: RecordReviewStatus,
    deposited_by: int | None = None,
):
    """One artifact with a real digest, at a given review status.

    ``deposited_by`` writes ``calculation.created_by``, which is the first
    of the two ownership paths. Left ``None`` the calculation belongs to
    nobody, which is what makes the approved-artifact tests below a
    genuine test of the *approval* path rather than of ownership.
    """
    content = b"curator-approved artifact bytes"
    sha256 = hashlib.sha256(content).hexdigest()
    _, _, calculation = _make_species_owned_calc(db_session)
    calculation.created_by = deposited_by
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


def _link_submission(
    db_session,
    *,
    calculation_id: int,
    owner_id: int,
    status: SubmissionStatus = SubmissionStatus.pending,
) -> Submission:
    """Give ``owner_id`` a submission that deposited ``calculation_id``.

    The second ownership path, and the one ADR 0018 names: a deposit is
    owned by the ``created_by`` of the submission that made it, which is
    not necessarily whoever ran the process that wrote the rows.
    """
    submission = Submission(
        created_by=owner_id,
        submission_kind=SubmissionKind.conformer,
        source_kind=SubmissionSourceKind.api,
        status=status,
    )
    db_session.add(submission)
    db_session.flush()
    db_session.add(
        SubmissionRecordLink(
            submission_id=submission.id,
            record_type=SubmissionRecordType.calculation,
            record_id=calculation_id,
        )
    )
    db_session.flush()
    return submission


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
    client, db_session, monkeypatch, _api_other_user
) -> None:
    """A stranger still cannot tell an unapproved digest from an unknown one.

    Rewritten 2026-08-24. It used to assert that *nobody* could download
    an unapproved artifact, which was true and was the defect: the
    depositor was in "nobody". The claim that survives is the narrower and
    correct one — the refusal applies to a caller who did not deposit it,
    and it is a 404, not a 403, so the response is not an existence
    oracle. Asserted by comparing the answer to a digest that genuinely
    does not exist, which is what a 403 regression could not survive.
    """
    artifact, _content = _downloadable_artifact(
        db_session,
        status=RecordReviewStatus.under_review,
        deposited_by=_api_other_user,
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
    unknown = client.get(f"/api/v1/scientific/artifacts/{'e' * 64}/download")

    assert response.status_code == 404
    assert called is False
    # Same status *and* same body: nothing in the answer says "this exists".
    assert (response.status_code, response.json()) == (
        unknown.status_code,
        unknown.json(),
    ), (response.text, unknown.text)


def test_depositor_downloads_their_own_unapproved_artifact(
    client, db_session, monkeypatch, _api_test_user
) -> None:
    """The justifying case: your own file, before any curator looked at it.

    Refused before 2026-08-24, and it is the whole point of the change.
    Approval is a curator action on a store where curation has barely
    begun, so gating a depositor's own bytes behind it locked every
    depositor out of every file they had ever uploaded.
    """
    artifact, content = _downloadable_artifact(
        db_session,
        status=RecordReviewStatus.not_reviewed,
        deposited_by=_api_test_user,
    )
    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes",
        lambda *_a, **_k: content,
    )

    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 200, response.text
    assert response.content == content
    assert response.headers["x-content-sha256"] == artifact.sha256
    assert response.headers["cache-control"] == "private, no-store"


def test_submission_owner_downloads_a_deposit_they_did_not_write(
    client, db_session, monkeypatch, _api_test_user, _api_other_user
) -> None:
    """Ownership is the submission's ``created_by``, not the row writer's.

    This is the case that decides between the two candidate notions of
    "owner", and it is not hypothetical: a submission made on someone's
    behalf — an agent, a group account, a pipeline running under a service
    identity — writes ``calculation.created_by`` as itself while the
    accountable principal is the one who owns the submission. ADR 0018
    puts ownership on ``submission.created_by`` for exactly this reason,
    and the upload route already authorizes attachment the same way.
    """
    artifact, content = _downloadable_artifact(
        db_session,
        status=RecordReviewStatus.not_reviewed,
        deposited_by=_api_other_user,
    )
    _link_submission(
        db_session,
        calculation_id=artifact.calculation_id,
        owner_id=_api_test_user,
    )
    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes",
        lambda *_a, **_k: content,
    )

    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 200, response.text
    assert response.content == content


def test_a_retired_submission_does_not_carry_download_ownership(
    client, db_session, monkeypatch, _api_test_user, _api_other_user
) -> None:
    """A rejected submission is no longer live lineage — on both paths.

    The upload route already refuses to let a rejected submission's owner
    attach artifacts to the calculations it once produced. The download
    path is the *same* predicate, so it inherits that judgement rather
    than inventing a second, more generous one. The depositor of the
    calculation itself is unaffected; what is refused here is authority
    held only by way of a submission that has been retired.
    """
    artifact, _content = _downloadable_artifact(
        db_session,
        status=RecordReviewStatus.not_reviewed,
        deposited_by=_api_other_user,
    )
    curator = AppUser(username="dl-rejecter", role=AppUserRole.curator)
    db_session.add(curator)
    db_session.flush()
    submission = _link_submission(
        db_session,
        calculation_id=artifact.calculation_id,
        owner_id=_api_test_user,
        status=SubmissionStatus.pending,
    )
    submission.status = SubmissionStatus.rejected
    submission.rejection_reason = "not this time"
    submission.rejected_by = curator.id
    db_session.flush()

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

    assert response.status_code == 404, response.text
    assert called is False


def test_owner_download_still_verifies_stored_bytes(
    client, db_session, monkeypatch, _api_test_user
) -> None:
    """Integrity is a claim about the store, not about who is asking.

    The ownership path must not become a way to receive bytes that no
    longer match their digest — and the custody break it discovers is
    recorded exactly as on the approved path (ADR 0014).
    """
    artifact, content = _downloadable_artifact(
        db_session,
        status=RecordReviewStatus.not_reviewed,
        deposited_by=_api_test_user,
    )
    observed = hashlib.sha256(b"tampered").hexdigest()

    def fake_load(sha256: str, *, expected_bytes: int | None = None) -> bytes:
        # The verification that runs on the approved path runs here too:
        # the route hands the store the expected size, and the store's
        # digest check is what raises.
        assert expected_bytes == len(content)
        raise ArtifactIntegrityError(
            "corrupt",
            finding=ArtifactIntegrityFinding.digest_mismatch,
            sha256=sha256,
            observed_sha256=observed,
            expected_bytes=len(content),
        )

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", fake_load
    )
    _record_into(monkeypatch, db_session)

    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )

    assert response.status_code == 502, response.text
    assert response.json()["code"] == "artifact_integrity_failed", response.text
    (event,) = _events_for(db_session, artifact.sha256)
    assert event.observed_sha256 == observed
    assert event.detected_during is ArtifactIntegrityDetectionContext.download


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
    """A row referencing an object the store says is gone is a custody break.

    And it answers as one. Until #226 this case and the outage below both
    came back ``(503, artifact_storage_unavailable)`` with "Retry later.",
    which is a false instruction here: the store has answered, it will
    keep answering the same way, and no amount of backing off puts the
    bytes back. The pair is asserted, not the status alone — a status on
    its own cannot distinguish this from its sibling break.
    """
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

    body = response.json()
    assert (response.status_code, body["code"]) == (502, "artifact_object_missing"), (
        response.text
    )
    assert body["detail"] == (
        "The stored bytes for this artifact are not in the object store. "
        "This has been recorded; retrying will not clear it."
    )
    # No database primary key in a user-facing body (DR-0028 Req 2).
    assert body["context"] == {}, body
    assert str(artifact.id) not in body["detail"], body

    (event,) = _events_for(db_session, artifact.sha256)
    assert event.finding is ArtifactIntegrityFinding.object_missing
    assert event.observed_sha256 is None


def test_unreachable_store_records_nothing(client, db_session, monkeypatch) -> None:
    """An endpoint that never answered says nothing about the object.

    The opposite fact for custody: recording a break here would
    manufacture a hard fail out of a network blip. And, since #226, the
    opposite answer too — this is the branch that keeps
    ``artifact_storage_unavailable`` and its "retry later", because here
    retrying is exactly right.
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

    body = response.json()
    assert (response.status_code, body["code"]) == (
        503,
        "artifact_storage_unavailable",
    ), response.text
    assert "Retry later." in body["detail"], body
    assert _events_for(db_session, artifact.sha256) == []


def test_the_two_storage_failures_do_not_answer_the_same_way(
    client, db_session, monkeypatch
) -> None:
    """The whole point of #226, asserted as a contrast in one test.

    The two tests above can both pass while the route answers identically
    if either is edited to match the other; what has to hold is that the
    answers *differ*, and differ in the direction that carries the retry
    advice. So both situations are provoked here, through the wire,
    against the same artifact, and the two ``(status, code)`` pairs are
    compared to each other rather than to a literal.

    A test that asserted only "an error arrived" would pass against a
    handler that fails on everything; a test that asserted only two
    literals would pass against a handler that had lost the branch and
    been re-pinned. Comparing the pairs is what neither can survive.
    """
    artifact, _content = _downloadable_artifact(
        db_session, status=RecordReviewStatus.approved
    )
    _record_into(monkeypatch, db_session)
    url = f"/api/v1/scientific/artifacts/{artifact.sha256}/download"

    def raising(**kwargs):
        def fake_load(*_args, **_kw):
            raise ArtifactStorageUnavailable("Artifact storage read failed", **kwargs)

        return fake_load

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes",
        raising(missing=True),
    )
    gone = client.get(url)

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes", raising()
    )
    outage = client.get(url)

    gone_pair = (gone.status_code, gone.json()["code"])
    outage_pair = (outage.status_code, outage.json()["code"])
    assert gone_pair != outage_pair, (gone.text, outage.text)
    # And in the right direction: only one of them may say "retry".
    assert outage_pair[0] == 503 and gone_pair[0] != 503, (gone_pair, outage_pair)
    assert "Retry later." in outage.json()["detail"]
    assert "will not clear it" in gone.json()["detail"]


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
    client, db_session, monkeypatch, _api_test_user, _api_other_user
) -> None:
    """A regular authenticated user (the default client actor) may pull
    approved bytes — the gate requires authentication, not a curator role.

    Amended 2026-08-24 to pin the actor as a *non-owner*: the calculation
    is deposited by someone else, and the ownership predicate is asserted
    to say so before the request is made. Without that, the new ownership
    path could silently be what serves these bytes, and a regression that
    lost approval-based access entirely would still pass here.
    """
    artifact, content = _downloadable_artifact(
        db_session,
        status=RecordReviewStatus.approved,
        deposited_by=_api_other_user,
    )
    actor = db_session.get(AppUser, _api_test_user)
    assert (
        user_owns_calculation_deposit(db_session, artifact.calculation, actor)
        is False
    ), "actor must not own this deposit, or the test proves nothing about approval"

    monkeypatch.setattr(
        "app.api.routes.scientific.artifacts.load_artifact_bytes",
        lambda *_a, **_k: content,
    )
    response = client.get(
        f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
    )
    assert response.status_code == 200
    assert response.content == content


def test_anonymous_is_refused_even_bytes_it_would_own_if_it_authenticated(
    db_session, monkeypatch, _api_test_user
) -> None:
    """Ownership is a reason to serve an *authenticated* caller. Only that.

    The property this asserts is the one the ownership change must leave
    untouched: no download path became anonymous. The artifact here is
    unapproved and deposited by the seeded API user — the exact row that
    user now downloads successfully one test above — and the request
    carries no credential, so it must be refused by the auth gate before
    any ownership question is even asked, and before any byte is loaded.

    ADR 0004 is why: an unredacted log carries producer-side scratch
    paths, usernames and scheduler ids, and that is a property of the ESS
    output format rather than something scrubbable at rest. Public access
    is a separate design (scrub-on-download), not this.
    """
    artifact, _content = _downloadable_artifact(
        db_session,
        status=RecordReviewStatus.not_reviewed,
        deposited_by=_api_test_user,
    )
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
        response = anon.get(
            f"/api/v1/scientific/artifacts/{artifact.sha256}/download"
        )

    assert response.status_code == 401, response.text
    assert called is False
