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
    latest_integrity_observations,
    record_from_error,
    record_integrity_observation,
    record_integrity_verified,
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

    record_integrity_observation(
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

    record_integrity_observation(
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

    record_integrity_observation(
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

    record_integrity_observation(
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
        record_integrity_observation(
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

    record_integrity_observation(
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
    event_id = record_integrity_observation(
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
    record_integrity_observation(
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


def test_a_later_verification_supersedes_an_earlier_break(db_session) -> None:
    """Latest observation wins, and the break is not erased to make it so.

    "Any break ever" would leave a record condemned after its evidence
    was restored — a trap rather than a judgement — and deleting the
    break to clear it would destroy the account of what happened.
    """
    sha = hashlib.sha256(b"repairable").hexdigest()
    record_integrity_observation(
        sha256=sha,
        finding=ArtifactIntegrityFinding.digest_mismatch,
        detected_during=ArtifactIntegrityDetectionContext.download,
        observed_sha256="a" * 64,
        session_factory=_SessionProxy(db_session),
        storage_client=_HeadClient(),
    )
    assert digests_with_recorded_breaks(db_session, [sha]) == frozenset({sha})

    record_integrity_verified(
        sha256=sha,
        detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
        observed_bytes=10,
        session_factory=_SessionProxy(db_session),
        storage_client=_HeadClient(),
    )

    assert digests_with_recorded_breaks(db_session, [sha]) == frozenset()
    events = _events(db_session, sha)
    assert [e.finding for e in events] == [
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityFinding.verified,
    ]
    # The clearing row carries its own proof.
    assert events[-1].observed_sha256 == sha


def test_a_break_after_a_verification_condemns_the_record_again(db_session) -> None:
    """Custody can break twice. The log is a sequence, not a latch."""
    sha = hashlib.sha256(b"twice").hexdigest()
    for finding, observed in (
        (ArtifactIntegrityFinding.digest_mismatch, "b" * 64),
        (ArtifactIntegrityFinding.verified, sha),
        (ArtifactIntegrityFinding.digest_mismatch, "c" * 64),
    ):
        record_integrity_observation(
            sha256=sha,
            finding=finding,
            detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
            observed_sha256=observed,
            session_factory=_SessionProxy(db_session),
            storage_client=_HeadClient(),
        )

    assert digests_with_recorded_breaks(db_session, [sha]) == frozenset({sha})


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


# ---------------------------------------------------------------------------
# One fact, three expressions of it
# ---------------------------------------------------------------------------
#
# "The latest observation for this digest" is the fact that decides
# whether a calculation hard-fails, and it is computed three ways that
# cannot be collapsed into one:
#
#   1. :func:`latest_integrity_observations` -- the owner. One batched
#      query returning whole rows, ordered by ``id``.
#   2. ``CalculationArtifact.integrity_events[-1]`` -- what the trust
#      evaluator reads. A ``lazy="selectin"`` relationship, so custody
#      reaches every read path without a per-call-site loader option and
#      without the evaluator needing a session.
#   3. ``max(id)`` in the read surface's ``_summaries`` -- an aggregate,
#      because a list endpoint over the whole custody record paginates
#      and cannot load every observation to fold in Python.
#
# Each exists for a property the others do not have, so the honest
# consolidation was to make the fourth (``digests_with_recorded_breaks``)
# a projection of the first and to *prove* the remaining three agree
# rather than assert it in three comments. These tests generate a
# population containing every shape that could pull them apart --
# multiple observations per digest, break-then-verified-then-break
# sequences, one digest shared by artifact rows on unrelated
# calculations, and observations with a null ``artifact_id`` -- and
# compare the identity of the row each one selects, not merely the
# boolean derived from it. An ordering divergence that happened to agree
# on ``is_break`` would still be a divergence.


def _observation_population(session):
    """Build a custody record with every shape that could split the three.

    Deterministic, not random: the interesting cases here are structural
    (a shared digest, a rowless digest, a trailing ``verified``) and are
    enumerated rather than sampled, so a failure names a shape instead of
    a seed.
    """
    from app.db.models.calculation import ArtifactIntegrityEvent

    _, _, calc_a = _make_species_owned_calc(db_session=session)
    _, _, calc_b = _make_species_owned_calc(db_session=session)

    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    #: ``(digest, [(finding, artifact_id_is_null)])``, oldest first.
    sequences = {
        # Broken once and never revisited.
        digest("broken-once"): [(ArtifactIntegrityFinding.digest_mismatch, False)],
        # Broken, then repaired: currently sound.
        digest("repaired"): [
            (ArtifactIntegrityFinding.digest_mismatch, False),
            (ArtifactIntegrityFinding.verified, False),
        ],
        # Broken, repaired, broken again: currently broken, and no
        # current-state summary can express the sequence.
        digest("recurring"): [
            (ArtifactIntegrityFinding.digest_mismatch, False),
            (ArtifactIntegrityFinding.verified, False),
            (ArtifactIntegrityFinding.size_mismatch, False),
        ],
        # The object was gone, then came back.
        digest("returned"): [
            (ArtifactIntegrityFinding.object_missing, False),
            (ArtifactIntegrityFinding.verified, False),
        ],
        # Recorded on the dedup path: no artifact row will ever exist.
        digest("rowless"): [(ArtifactIntegrityFinding.digest_mismatch, True)],
        # A break discovered with no row, then a repair recorded with one.
        digest("rowless-then-rowed"): [
            (ArtifactIntegrityFinding.digest_mismatch, True),
            (ArtifactIntegrityFinding.verified, False),
        ],
    }

    # One digest shared by artifact rows on two unrelated calculations --
    # the point of content addressing, and the case a per-row foreign key
    # would have got wrong.
    shared = digest("shared-by-two-calculations")
    sequences[shared] = [
        (ArtifactIntegrityFinding.digest_mismatch, False),
        (ArtifactIntegrityFinding.verified, False),
        (ArtifactIntegrityFinding.digest_mismatch, False),
    ]

    artifacts: dict[str, int] = {}
    for token, calculation in (
        ("broken-once", calc_a),
        ("repaired", calc_a),
        ("recurring", calc_a),
        ("returned", calc_b),
        ("rowless-then-rowed", calc_b),
    ):
        row = attach_artifact(
            session,
            calculation=calculation,
            sha256=digest(token),
            filename=f"{token}.log",
        )
        artifacts[digest(token)] = row.id
    for calculation in (calc_a, calc_b):
        row = attach_artifact(
            session,
            calculation=calculation,
            sha256=shared,
            filename="shared.log",
        )
        artifacts.setdefault(shared, row.id)

    for sha, steps in sequences.items():
        for finding, rowless in steps:
            session.add(
                ArtifactIntegrityEvent(
                    sha256=sha,
                    artifact_id=None if rowless else artifacts.get(sha),
                    finding=finding,
                    detected_during=(
                        ArtifactIntegrityDetectionContext.store_dedup_verification
                        if rowless
                        else ArtifactIntegrityDetectionContext.verification_sweep
                    ),
                    observed_sha256=(
                        sha
                        if finding is ArtifactIntegrityFinding.verified
                        else (
                            None
                            if finding is ArtifactIntegrityFinding.object_missing
                            else digest(f"{sha}-observed")
                        )
                    ),
                    expected_bytes=1,
                    observed_bytes=(
                        None
                        if finding is ArtifactIntegrityFinding.object_missing
                        else 2
                    ),
                    detail=f"{finding.value} on {sha[:8]}",
                )
            )
            session.flush()
    return list(sequences)


def test_the_owner_and_the_relationship_select_the_same_observation(db_session):
    """The trust evaluator's expression must agree with the owner.

    This is the one that matters: ``integrity_events[-1]`` is what
    decides ``HardFailReason.artifact_integrity_failed``. Compared by row
    identity rather than by ``is_break``, because two orderings that
    happen to agree on the boolean today are still two orderings.
    """
    from app.db.models.calculation import CalculationArtifact

    digests = _observation_population(db_session)
    owner = latest_integrity_observations(db_session, digests)

    artifacts = db_session.scalars(
        select(CalculationArtifact).where(CalculationArtifact.sha256.in_(digests))
    ).all()
    assert artifacts, "the population must include artifact rows to compare against"

    for artifact in artifacts:
        assert artifact.integrity_events, artifact.sha256
        assert artifact.integrity_events[-1].id == owner[artifact.sha256].id


def test_every_expression_agrees_on_which_digests_are_currently_broken(db_session):
    """The boolean each surface publishes, from all four expressions."""
    from app.db.models.calculation import CalculationArtifact
    from app.services.scientific_read.artifact_integrity_reads import _summaries

    digests = _observation_population(db_session)
    owner = latest_integrity_observations(db_session, digests)

    expected = frozenset(
        sha for sha, event in owner.items() if event.finding.is_break
    )
    # The population must actually contain both answers, or "they agree"
    # is satisfied by everything being sound.
    assert expected
    assert set(digests) - expected

    assert digests_with_recorded_breaks(db_session, digests) == expected

    records, _total = _summaries(
        db_session,
        sha256=None,
        calculation_ref=None,
        only_currently_broken=False,
        offset=0,
        limit=200,
    )
    summarised = {
        record.sha256: record for record in records if record.sha256 in owner
    }
    assert set(summarised) == set(owner)
    for sha, record in summarised.items():
        assert record.currently_broken is owner[sha].finding.is_break, sha
        # The read surface reports the latest observation's own contents,
        # so it must be the same row and not merely the same verdict.
        assert record.latest.detail == owner[sha].detail, sha

    artifacts = db_session.scalars(
        select(CalculationArtifact).where(CalculationArtifact.sha256.in_(digests))
    ).all()
    for artifact in artifacts:
        broken = artifact.integrity_events[-1].finding.is_break
        assert broken is (artifact.sha256 in expected), artifact.sha256


def test_a_digest_with_no_artifact_row_is_still_the_records_own_business(db_session):
    """The rowless digest is in the record and reachable, but condemns nothing.

    ``store_dedup_verification`` records against an object whose
    referencing row is being refused. The owner and the read surface must
    both see it; the relationship cannot, and should not -- there is no
    calculation to hard-fail.
    """
    from app.db.models.calculation import CalculationArtifact
    from app.services.scientific_read.artifact_integrity_reads import _summaries

    digests = _observation_population(db_session)
    rowless = hashlib.sha256(b"rowless").hexdigest()
    assert rowless in digests

    assert rowless in latest_integrity_observations(db_session, digests)
    assert rowless in digests_with_recorded_breaks(db_session, digests)

    records, _total = _summaries(
        db_session,
        sha256=rowless,
        calculation_ref=None,
        only_currently_broken=False,
        offset=0,
        limit=10,
    )
    assert [record.sha256 for record in records] == [rowless]

    assert (
        db_session.scalars(
            select(CalculationArtifact).where(CalculationArtifact.sha256 == rowless)
        ).all()
        == []
    )
