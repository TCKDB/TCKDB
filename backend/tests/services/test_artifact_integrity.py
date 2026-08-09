"""Tests for the durable record of custody breaks (ADR 0014).

A log line is not a record. These tests are about what makes the
difference: an ``artifact_integrity_event`` row that carries expected
versus observed, the store's own metadata for the object, and enough to
tell the three causes of a digest mismatch apart.
"""

from __future__ import annotations

import contextlib
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import ArtifactIntegrityEvent
from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
)
from app.services.artifact_integrity import (
    digests_with_recorded_breaks,
    record_from_error,
    record_integrity_failure,
)
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
    load_artifact_bytes,
)
from tests.api.scientific.test_api_scientific_artifacts import _make_species_owned_calc
from tests.services.scientific_read._factories import attach_artifact


class _SessionProxy:
    """Hand the recorder the test's own session with its commit neutered.

    The recorder deliberately opens its *own* transaction so the record
    survives the request that discovered it — that durability claim has
    its own test below, against a real engine. Everywhere else the thing
    under test is the row's *content*, and letting each of those tests
    really commit would leave rows behind for siblings to trip over.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def begin(self):
        return contextlib.nullcontext()


class _HeadClient:
    """Minimal S3 stand-in that answers only ``head_object``."""

    def __init__(self, **metadata) -> None:
        self.metadata = metadata
        self.calls: list[str] = []

    def head_object(self, *, Bucket, Key):
        self.calls.append(Key)
        if self.metadata is None:
            raise RuntimeError("no metadata")
        return dict(self.metadata)


class _BytesBody:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self) -> bytes:
        return self.content


class _ReadClient:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def get_object(self, **_kwargs):
        return {"Body": _BytesBody(self.content)}


def _events(session: Session, sha256: str) -> list[ArtifactIntegrityEvent]:
    return list(
        session.scalars(
            select(ArtifactIntegrityEvent)
            .where(ArtifactIntegrityEvent.sha256 == sha256)
            .order_by(ArtifactIntegrityEvent.id)
        ).all()
    )


# ---------------------------------------------------------------------------
# The exception carries the comparison as data, not only as prose
# ---------------------------------------------------------------------------


def test_digest_mismatch_error_carries_structured_fields() -> None:
    """A recorder must not have to re-parse an f-string to write its row."""
    content = b"the bytes we meant to store"
    sha = hashlib.sha256(content).hexdigest()

    with pytest.raises(ArtifactIntegrityError) as caught:
        load_artifact_bytes(sha, client=_ReadClient(b"not those bytes"))

    error = caught.value
    assert error.finding is ArtifactIntegrityFinding.digest_mismatch
    assert error.sha256 == sha
    assert error.observed_sha256 == hashlib.sha256(b"not those bytes").hexdigest()
    assert error.observed_bytes == len(b"not those bytes")


def test_size_mismatch_error_is_a_distinct_finding() -> None:
    """A short read is not the same observation as a swapped object."""
    content = b"exactly these bytes"
    sha = hashlib.sha256(content).hexdigest()

    with pytest.raises(ArtifactIntegrityError) as caught:
        load_artifact_bytes(
            sha, expected_bytes=len(content) + 100, client=_ReadClient(content)
        )

    assert caught.value.finding is ArtifactIntegrityFinding.size_mismatch
    assert caught.value.expected_bytes == len(content) + 100
    assert caught.value.observed_bytes == len(content)


# ---------------------------------------------------------------------------
# The record itself
# ---------------------------------------------------------------------------


def test_detection_produces_a_row_carrying_expected_versus_observed(
    db_session,
) -> None:
    content = b"stored evidence"
    sha = hashlib.sha256(content).hexdigest()
    _, _, calculation = _make_species_owned_calc(db_session)
    artifact = attach_artifact(db_session, calculation=calculation)
    artifact.sha256 = sha
    artifact.bytes = len(content)
    db_session.flush()

    with pytest.raises(ArtifactIntegrityError) as caught:
        load_artifact_bytes(
            sha, expected_bytes=len(content), client=_ReadClient(b"swapped")
        )

    record_from_error(
        caught.value,
        detected_during=ArtifactIntegrityDetectionContext.download,
        artifact=artifact,
        session_factory=_SessionProxy(db_session),
        storage_client=_HeadClient(),
    )

    (event,) = _events(db_session, sha)
    assert event.finding is ArtifactIntegrityFinding.digest_mismatch
    assert event.detected_during is ArtifactIntegrityDetectionContext.download
    assert event.sha256 == sha
    assert event.observed_sha256 == hashlib.sha256(b"swapped").hexdigest()
    assert event.expected_bytes == len(content)
    assert event.observed_bytes == len(b"swapped")
    assert event.artifact_id == artifact.id
    assert event.artifact_recorded_at == artifact.created_at


def test_row_captures_the_stores_own_metadata_so_causes_can_be_told_apart(
    db_session,
) -> None:
    """The three causes need three different remedies; the row must separate them.

    Here ``LastModified`` is well after the artifact was recorded, which
    is the signature of "the object was modified after we wrote it" as
    opposed to "we never stored what we said we did".
    """
    content = b"stored evidence"
    sha = hashlib.sha256(content).hexdigest()
    _, _, calculation = _make_species_owned_calc(db_session)
    artifact = attach_artifact(db_session, calculation=calculation)
    artifact.sha256 = sha
    artifact.bytes = len(content)
    db_session.flush()

    overwritten_at = datetime.now(timezone.utc) + timedelta(days=30)
    client = _HeadClient(
        LastModified=overwritten_at,
        ETag='"deadbeefdeadbeefdeadbeefdeadbeef"',
        ContentLength=7,
    )

    record_integrity_failure(
        sha256=sha,
        finding=ArtifactIntegrityFinding.digest_mismatch,
        detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
        observed_sha256=hashlib.sha256(b"swapped").hexdigest(),
        expected_bytes=len(content),
        observed_bytes=7,
        artifact_id=artifact.id,
        artifact_recorded_at=artifact.created_at,
        session_factory=_SessionProxy(db_session),
        storage_client=client,
    )

    (event,) = _events(db_session, sha)
    assert event.object_etag == "deadbeefdeadbeefdeadbeefdeadbeef"
    assert event.object_content_length == 7
    # Naive UTC, comparable against ``artifact_recorded_at`` — the whole
    # point of the column. An aware value stored raw would make this
    # comparison wrong by the server's offset.
    assert event.object_last_modified_at is not None
    assert event.object_last_modified_at.tzinfo is None
    assert event.object_last_modified_at > event.artifact_recorded_at


def test_missing_object_records_no_observed_digest(db_session) -> None:
    """An object that was not read cannot report what it read.

    Guarded in the schema, not only in the recorder, so a future writer
    cannot record an alarm without its evidence.
    """
    content = b"gone"
    sha = hashlib.sha256(content).hexdigest()
    _, _, calculation = _make_species_owned_calc(db_session)
    artifact = attach_artifact(db_session, calculation=calculation)
    artifact.sha256 = sha
    db_session.flush()

    record_integrity_failure(
        sha256=sha,
        finding=ArtifactIntegrityFinding.object_missing,
        detected_during=ArtifactIntegrityDetectionContext.download,
        artifact_id=artifact.id,
        session_factory=_SessionProxy(db_session),
        storage_client=_HeadClient(),
    )

    (event,) = _events(db_session, sha)
    assert event.finding is ArtifactIntegrityFinding.object_missing
    assert event.observed_sha256 is None


def test_record_is_keyed_on_the_digest_so_it_finds_the_sharing_row(
    db_session,
) -> None:
    """A dedup-time break has no row of its own; it must still name one.

    The store is content-addressed, so a corrupt object discovered while
    refusing a *new* upload is already referenced by whatever committed
    rows share the digest. The record has to say so, or the alarming
    case — an existing record resting on the corrupt object — goes
    unreported.
    """
    content = b"shared object"
    sha = hashlib.sha256(content).hexdigest()
    _, _, calculation = _make_species_owned_calc(db_session)
    existing = attach_artifact(db_session, calculation=calculation)
    existing.sha256 = sha
    existing.bytes = len(content)
    db_session.flush()

    record_integrity_failure(
        sha256=sha,
        finding=ArtifactIntegrityFinding.digest_mismatch,
        detected_during=(
            ArtifactIntegrityDetectionContext.store_dedup_verification
        ),
        observed_sha256=hashlib.sha256(b"corrupt").hexdigest(),
        observed_bytes=7,
        # No artifact known: the upload that would have created one is
        # being refused.
        session_factory=_SessionProxy(db_session),
        storage_client=_HeadClient(),
    )

    (event,) = _events(db_session, sha)
    assert event.artifact_id == existing.id
    assert event.artifact_recorded_at == existing.created_at
    assert event.expected_bytes == existing.bytes


def test_one_corrupt_object_condemns_every_row_that_shares_it(db_session) -> None:
    """Content-addressed corruption is per-object, not per-row.

    A per-row foreign key would have let the calculation whose download
    discovered the break hard-fail while its twins in unrelated
    calculations went on reading as sound.
    """
    content = b"one object, two records"
    sha = hashlib.sha256(content).hexdigest()
    _, _, calc_a = _make_species_owned_calc(db_session)
    _, _, calc_b = _make_species_owned_calc(db_session)
    artifact_a = attach_artifact(db_session, calculation=calc_a)
    artifact_b = attach_artifact(db_session, calculation=calc_b)
    for row in (artifact_a, artifact_b):
        row.sha256 = sha
        row.bytes = len(content)
    db_session.flush()

    record_integrity_failure(
        sha256=sha,
        finding=ArtifactIntegrityFinding.digest_mismatch,
        detected_during=ArtifactIntegrityDetectionContext.download,
        observed_sha256=hashlib.sha256(b"x").hexdigest(),
        artifact_id=artifact_a.id,
        session_factory=_SessionProxy(db_session),
        storage_client=_HeadClient(),
    )
    db_session.expire_all()

    assert len(db_session.get(type(artifact_a), artifact_a.id).integrity_events) == 1
    assert len(db_session.get(type(artifact_b), artifact_b.id).integrity_events) == 1


def test_recording_failure_never_masks_the_integrity_break(db_session) -> None:
    """If the database is also unhappy, the caller still gets their error.

    A custody break reported as an ORM traceback is a custody break
    nobody will find.
    """

    class _ExplodingFactory:
        def __call__(self):
            raise RuntimeError("database is on fire")

    assert (
        record_integrity_failure(
            sha256="a" * 64,
            finding=ArtifactIntegrityFinding.digest_mismatch,
            detected_during=ArtifactIntegrityDetectionContext.download,
            observed_sha256="b" * 64,
            session_factory=_ExplodingFactory(),
            storage_client=_HeadClient(),
        )
        is None
    )


def test_head_probe_failure_still_produces_a_row(db_session) -> None:
    """A store that cannot even be asked for metadata loses the discriminators,
    not the record. A missing discriminator is a gap; a missing record is
    the failure this whole change exists to end."""

    class _DeadClient:
        def head_object(self, **_kwargs):
            raise RuntimeError("connection refused")

    record_integrity_failure(
        sha256="c" * 64,
        finding=ArtifactIntegrityFinding.digest_mismatch,
        detected_during=ArtifactIntegrityDetectionContext.download,
        observed_sha256="d" * 64,
        session_factory=_SessionProxy(db_session),
        storage_client=_DeadClient(),
    )

    (event,) = _events(db_session, "c" * 64)
    assert event.object_last_modified_at is None
    assert event.object_etag is None


def test_record_survives_the_transaction_that_discovered_it(
    db_engine, db_session
) -> None:
    """The durability claim, against a real engine.

    The download route runs on a read session that never commits and
    discovers the break on its way to returning an error. If the record
    rode along on that transaction, the one thing that must survive
    would be the one thing that is discarded.
    """
    sha = hashlib.sha256(b"durability").hexdigest()
    event_id = record_integrity_failure(
        sha256=sha,
        finding=ArtifactIntegrityFinding.digest_mismatch,
        detected_during=ArtifactIntegrityDetectionContext.download,
        observed_sha256="e" * 64,
        session_factory=lambda: Session(bind=db_engine),
        storage_client=_HeadClient(),
    )
    assert event_id is not None
    try:
        # A brand-new connection: nothing of the test's transaction is
        # visible here, so seeing the row proves it really committed.
        with Session(db_engine) as fresh:
            event = fresh.get(ArtifactIntegrityEvent, event_id)
            assert event is not None
            assert event.sha256 == sha
    finally:
        with Session(db_engine) as cleanup:
            row = cleanup.get(ArtifactIntegrityEvent, event_id)
            if row is not None:
                cleanup.delete(row)
                cleanup.commit()


def test_dedup_integrity_break_escapes_as_itself_and_is_recorded(
    db_session, monkeypatch
) -> None:
    """``store_artifact`` verifies the object it is about to share.

    When that verification fails the object already in the store is
    corrupt, and the rows already pointing at it are affected — not just
    this upload. Relabelling it ``ArtifactStorageUnavailable`` told the
    uploader to retry a condition retrying cannot fix, and discarded the
    one moment TCKDB had noticed.
    """
    import base64

    from app.db.models.common import ArtifactKind
    from app.schemas.fragments.artifact import ArtifactIn
    from app.services import artifact_persistence

    _, _, calculation = _make_species_owned_calc(db_session)
    content = b"Entering Gaussian System, corrupt twin\n" * 4
    sha = hashlib.sha256(content).hexdigest()
    existing = attach_artifact(db_session, calculation=calculation)
    existing.sha256 = sha
    existing.bytes = len(content)
    db_session.flush()

    def exploding_store(*_args, **_kwargs):
        raise ArtifactIntegrityError(
            f"Artifact digest verification failed for sha={sha}",
            finding=ArtifactIntegrityFinding.digest_mismatch,
            sha256=sha,
            observed_sha256="a" * 64,
            observed_bytes=3,
        )

    monkeypatch.setattr(artifact_persistence, "store_artifact", exploding_store)
    monkeypatch.setattr(
        "app.services.artifact_integrity.head_artifact_object", lambda *a, **k: None
    )
    monkeypatch.setattr("app.api.deps.SessionLocal", _SessionProxy(db_session))

    payload = ArtifactIn(
        kind=ArtifactKind.output_log,
        filename="twin.log",
        content_base64=base64.b64encode(content).decode(),
    )

    with pytest.raises(ArtifactIntegrityError):
        artifact_persistence.persist_artifact(
            db_session, calculation_id=calculation.id, artifact_in=payload
        )

    (event,) = _events(db_session, sha)
    assert event.detected_during is (
        ArtifactIntegrityDetectionContext.store_dedup_verification
    )
    # No row existed for the refused upload, so the record names the
    # committed row that already shares the corrupt object.
    assert event.artifact_id == existing.id


def test_digests_with_recorded_breaks_answers_a_batch_in_one_query(
    db_session,
) -> None:
    clean = hashlib.sha256(b"clean").hexdigest()
    broken = hashlib.sha256(b"broken").hexdigest()
    record_integrity_failure(
        sha256=broken,
        finding=ArtifactIntegrityFinding.digest_mismatch,
        detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
        observed_sha256="f" * 64,
        session_factory=_SessionProxy(db_session),
        storage_client=_HeadClient(),
    )

    assert digests_with_recorded_breaks(db_session, [clean, broken]) == frozenset(
        {broken}
    )
    assert digests_with_recorded_breaks(db_session, []) == frozenset()


# ---------------------------------------------------------------------------
# A store that does not answer is not evidence about the object
# ---------------------------------------------------------------------------


def test_missing_key_and_unreachable_store_are_distinguishable() -> None:
    """Both are 503 to a client, and opposite facts for custody.

    ``NoSuchKey`` for a digest a row still references is a break worth
    recording; an endpoint that never answered says nothing about the
    object and recording it would be a lie.
    """
    from botocore.exceptions import ClientError, EndpointConnectionError

    class _MissingClient:
        def get_object(self, **_kwargs):
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "nope"}}, "GetObject"
            )

    class _UnreachableClient:
        def get_object(self, **_kwargs):
            raise EndpointConnectionError(endpoint_url="http://127.0.0.1:9000")

    with pytest.raises(ArtifactStorageUnavailable) as missing:
        load_artifact_bytes("a" * 64, client=_MissingClient())
    assert missing.value.missing is True

    with pytest.raises(ArtifactStorageUnavailable) as unreachable:
        load_artifact_bytes("a" * 64, client=_UnreachableClient())
    assert unreachable.value.missing is False


def test_endpoint_connection_error_is_wrapped_not_leaked() -> None:
    """``EndpointConnectionError`` is not a ``ClientError``.

    Before this arm existed a real storage outage escaped
    ``load_artifact_bytes`` raw, sailed past every
    ``except ArtifactStorageUnavailable`` handler downstream, and
    surfaced as an unexplained 500 on the route that documents a 503.
    """
    from botocore.exceptions import ClientError, EndpointConnectionError

    assert not issubclass(EndpointConnectionError, ClientError)

    class _UnreachableClient:
        def get_object(self, **_kwargs):
            raise EndpointConnectionError(endpoint_url="http://127.0.0.1:9000")

    with pytest.raises(ArtifactStorageUnavailable, match="127.0.0.1:9000"):
        load_artifact_bytes("a" * 64, client=_UnreachableClient())
