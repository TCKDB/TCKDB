"""A full object store, told apart from an unreachable one and a lost object.

What was wrong
--------------
``app.services.artifact_storage`` special-cased exactly one condition — the
store answering "no such key" — and everything else a botocore
``ClientError`` can carry collapsed into ``ArtifactStorageUnavailable``,
which the API reports as ``503 ... Retry later.``

A **full** store landed in that residue. It is the one storage failure
where "retry later" is not merely unhelpful but wrong in a way the caller
can act on to their own cost: retrying a full disk fails until an
*operator* frees space, so a depositor is told to loop on a condition no
number of attempts can clear.

Why all four cases live in one file
----------------------------------
A test asserting only "a 5xx arrived" passes against a store that fails on
everything, and a suite that checks the new condition in one file and the
old ones in another cannot show that the three were ever told apart. So the
three failure conditions are asserted here as ``(status, code)`` pairs,
next to a **healthy** store accepting the same upload:

===================  ======  ==============================
condition            status  code
===================  ======  ==============================
healthy              201     (no error body)
full                 507     ``artifact_storage_full``
unreachable          503     ``artifact_storage_unavailable``
object missing        502    ``artifact_object_missing``
===================  ======  ==============================

The error codes the "full" cases use are **measured**, not taken from
documentation: MinIO ``RELEASE.2025-09-07T16-13-09Z`` was filled on a
size-capped scratch volume and answered ``XMinioStorageFull`` at HTTP 507,
and refused a write with ``XMinioAdminBucketQuotaExceeded`` at HTTP 400
once a hard bucket quota had been passed. See ``_STORAGE_FULL_CODES``.

The ``/status`` half of the same defect is asserted in
``test_api_status.py`` — a full store is invisible to a read-only probe,
which is the more serious half and has its own tests.

What these tests cannot do, and what was done instead
-----------------------------------------------------
Only the transport is faked here, so ``store_artifact`` and the handler run
for real — but the *store* does not, and "the code set is right" is a
different claim from "the fix fires on the real condition". So the whole
chain was also run once against a scratch MinIO filled for real, through
real botocore: the upload answered ``507 artifact_storage_full``, ``/status``
went degraded naming ``XMinioStorageFull`` and the refused byte count, and
``head_object`` on the refused key answered 404 (no orphan). That run is
deliberately not committed — it needs a container on a fixed port and a
volume filled to its threshold, which is not something a unit suite should
do on every invocation.

It is worth knowing what that run showed, because it is counter-intuitive
and it is why the refused *size* is recorded: **"full" is not
all-or-nothing.** MinIO's threshold check is sized against the incoming
object, so the same store that refused an 8 MiB artifact accepted a 1-byte
write in the same second. Some uploads continuing to work is therefore not
evidence that the store is well.
"""

from __future__ import annotations

import base64
import hashlib
import io

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api import deps as api_deps
from app.api.routes import health
from app.db.models.artifact_storage_capacity import ArtifactStorageCapacityEvent
from app.services import artifact_storage
from app.services import artifact_storage_capacity as capacity
from app.services import artifact_storage_seaweedfs as seaweedfs

CONFORMER_PAYLOAD: dict = {
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
    "calculation": {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
    },
    "label": "h-conf-storage-capacity",
}

_BYTES = b"the artifact bytes a depositor is trying to deposit\n"
_SHA256 = hashlib.sha256(_BYTES).hexdigest()
_KEY = f"{_SHA256[:2]}/{_SHA256}"


def _artifact_request() -> dict:
    return {
        "artifacts": [
            {
                "kind": "ancillary",
                "filename": "note.txt",
                "content_base64": base64.b64encode(_BYTES).decode("ascii"),
                "sha256": _SHA256,
                "bytes": len(_BYTES),
            }
        ]
    }


# ---------------------------------------------------------------------------
# Object stores. Only the transport is faked: ``store_artifact`` runs for
# real against these, so the classification under test is the production one.
# ---------------------------------------------------------------------------


def _client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": f"scripted {code}"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


class _HealthyStore:
    """Accepts writes and serves them back."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(objects or {})

    def head_bucket(self, Bucket):  # boto3 kwarg casing
        return {}

    def create_bucket(self, Bucket):
        return {}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _client_error("404", 404, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _client_error("404", 404, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body
        return {}


class _FullStore(_HealthyStore):
    """Answers every read, refuses every write. What a full store *is*.

    Measured behaviour, and the reason this is not a subclass that fails
    everything: on a MinIO filled past its free-space threshold,
    ``head_bucket``, ``head_object``, ``get_object`` and ``list_objects``
    all succeed. Only the write is refused. A fake that broke reads too
    would let a wrong implementation pass.
    """

    def __init__(self, code: str = "XMinioStorageFull", status: int = 507) -> None:
        super().__init__()
        self._code = code
        self._status = status

    def put_object(self, Bucket, Key, Body, ContentType=None):
        raise _client_error(self._code, self._status, "PutObject")


class _UnreachableStore:
    """No HTTP response at all: the ordinary outage."""

    def head_bucket(self, Bucket):
        raise EndpointConnectionError(endpoint_url="http://minio.test:9000")


@pytest.fixture
def capacity_db(db_engine, monkeypatch):
    """One connection that the capacity log and ``/status`` both see.

    The observation used to be a module global that a fixture could reset
    between tests. It is a table now, so isolation is a transaction: a
    dedicated connection with an outer transaction rolled back at
    teardown, with both the ambient session factory and ``/status``'s
    bound to it so every party in a test reads the same rows.

    Both names are patched because they are two references to the same
    factory reached by different routes -- ``health`` holds a module-level
    import, while the capacity service looks it up lazily at call time.
    Patching one and not the other produces a test where the writer and
    the reader are on different connections and nothing is ever visible,
    which is a confusing way to fail.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    # Mirrors conftest: an open SAVEPOINT makes each Session's commit
    # release a savepoint rather than end the outer transaction.
    connection.begin_nested()
    factory = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(api_deps, "SessionLocal", factory)
    monkeypatch.setattr(health, "SessionLocal", factory)
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _no_seaweedfs_capacity_opinion_by_default(monkeypatch):
    """No test here asks a real SeaweedFS unless it says so.

    CI sets ``S3_SEAWEEDFS_MASTER_URL`` for the live tests, so without this
    an ``InternalError`` scripted below would be classified by whatever the
    CI store happens to say about its room. Unset is the pre-#545
    behaviour, and the tests that exercise the SeaweedFS arm set it back.
    """
    monkeypatch.setattr(artifact_storage, "S3_SEAWEEDFS_MASTER_URL", "")


def _outstanding(factory) -> capacity.StorageFullState | None:
    with factory() as session:
        return capacity.current_full_state(session)


@pytest.fixture
def use_store(monkeypatch):
    def _install(store):
        monkeypatch.setattr(
            artifact_storage, "_get_s3_client", lambda config=None: store
        )
        return store

    return _install


def _a_calculation(client) -> int:
    response = client.post("/api/v1/uploads/conformers", json=CONFORMER_PAYLOAD)
    assert response.status_code == 201, response.text
    return response.json()["primary_calculation"]["calculation_id"]


# ---------------------------------------------------------------------------
# The three failure conditions, and the accept-half
# ---------------------------------------------------------------------------


def test_a_healthy_store_accepts_the_upload(client, use_store) -> None:
    """The accept-half, without which none of the below proves anything.

    Every assertion in this file is satisfied by a store that fails on
    everything unless one case succeeds, and by a route that refuses every
    upload unless one upload lands.
    """
    store = use_store(_HealthyStore())
    calc_id = _a_calculation(client)

    response = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts", json=_artifact_request()
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["artifacts"]) == 1
    assert body["artifacts"][0]["sha256"] == _SHA256
    # The bytes really went in through the production write path.
    assert store.objects[_KEY] == _BYTES


@pytest.mark.parametrize(
    ("s3_code", "s3_status"),
    [
        # Measured: MinIO's drive free-space threshold, at HTTP 507.
        ("XMinioStorageFull", 507),
        # Measured: MinIO's hard bucket quota, at HTTP 400. TCKDB reports
        # 507 for this too -- the upstream 400 classifies MinIO's own API
        # call, not the depositor's request, and the depositor did nothing
        # wrong in either case.
        ("XMinioAdminBucketQuotaExceeded", 400),
        # Documented spellings from other S3-compatible implementations,
        # carried because an unrecognised spelling reproduces this defect
        # exactly.
        ("QuotaExceeded", 403),
        ("InsufficientStorage", 507),
    ],
)
def test_a_full_store_is_507_and_says_an_operator_must_act(
    client, use_store, s3_code, s3_status
) -> None:
    use_store(_FullStore(code=s3_code, status=s3_status))
    calc_id = _a_calculation(client)

    response = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts", json=_artifact_request()
    )
    assert response.status_code == 507, response.text
    body = response.json()
    assert body["code"] == "artifact_storage_full", body

    # The sentence a depositor reads has to contradict the retry they would
    # otherwise attempt, and name who can fix it.
    detail = body["detail"]
    assert "retrying will not clear it" in detail, detail
    assert "operator" in detail, detail

    # DR-0028 Req. 2 -- a subsystem is named and nothing else. No row ids,
    # no digest, no bucket, no endpoint, and no S3 error code: the store's
    # code is for the log and for /status, not for a public body.
    assert body["context"] == {}, body
    for leak in (str(calc_id), _SHA256, s3_code):
        assert leak not in detail, (leak, detail)

    # 507 is outside tckdb_client.retry.DEFAULT_RETRY_STATUS_CODES, which
    # is how a pinned client that has never heard of this code still stops
    # after one attempt. If this status ever moves into that set, the
    # advice above becomes unenforceable.
    assert response.status_code not in {429, 502, 503, 504}


def test_an_unreachable_store_is_still_503_and_still_says_retry(
    client, use_store
) -> None:
    """The condition whose 503 was right all along, and must not move."""
    use_store(_UnreachableStore())
    calc_id = _a_calculation(client)

    response = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts", json=_artifact_request()
    )
    assert response.status_code == 503, response.text
    body = response.json()
    assert body["code"] == "artifact_storage_unavailable", body
    assert "Retry later." in body["detail"], body
    assert body["context"] == {}, body


def test_a_missing_object_is_still_502_artifact_object_missing(
    client, db_session, monkeypatch
) -> None:
    """The third condition, reached through the route that can produce it.

    A missing object is a *read* fact, so it belongs to the download route
    rather than the upload one. It is asserted here anyway: the point of
    this file is that the three conditions get three answers, and a file
    that omitted one could not show that.
    """
    from app.db.models.common import RecordReviewStatus, SubmissionRecordType
    from tests.api.scientific.test_api_scientific_artifacts import (
        _make_species_owned_calc,
    )
    from tests.services.scientific_read._factories import attach_artifact, set_review

    _species, _entry, calculation = _make_species_owned_calc(db_session)
    artifact = attach_artifact(db_session, calculation=calculation)
    artifact.sha256 = _SHA256
    artifact.bytes = len(_BYTES)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calculation.id,
        status=RecordReviewStatus.approved,
    )
    db_session.flush()

    # An empty but perfectly healthy store: it answers, and says the key is
    # not there.
    monkeypatch.setattr(
        artifact_storage, "_get_s3_client", lambda config=None: _HealthyStore()
    )
    monkeypatch.setattr("app.api.deps.SessionLocal", lambda: db_session)

    response = client.get(f"/api/v1/scientific/artifacts/{_SHA256}/download")
    assert response.status_code == 502, response.text
    body = response.json()
    assert body["code"] == "artifact_object_missing", body
    assert body["context"] == {}, body


# ---------------------------------------------------------------------------
# The service-level classification, and the latch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("s3_code", "s3_status"),
    [
        ("AccessDenied", 403),
        # Measured against SeaweedFS 4.47: this is its answer to a full
        # store -- volume slots exhausted, the disk itself out of space
        # (ENOSPC), and a bucket that ``s3.bucket.quota.enforce`` made
        # read-only -- all three alike. It is also its answer to *any*
        # internal failure, so it cannot mean "full". See the SeaweedFS
        # note on ``_STORAGE_FULL_CODES``.
        ("InternalError", 500),
    ],
)
def test_a_refused_write_that_is_not_about_space_stays_unclassified(
    capacity_db, s3_code, s3_status
) -> None:
    """A refusal that does not *say* it is about space is not a full store.

    ``AccessDenied`` is a real outage-shaped failure. Without this,
    ``_STORAGE_FULL_CODES`` could be widened to "any ``ClientError`` on a
    write" and every test above would still pass, which would turn a
    credentials problem into "free some space".

    ``InternalError`` is the tempting one: it is literally what SeaweedFS
    returns when full. Admitting it would tell a depositor to wait for an
    operator on every transient SeaweedFS fault, and record a durable
    refusal that degrades ``/status`` until a large enough write succeeds.
    """
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        artifact_storage.store_artifact(
            _BYTES, _SHA256, client=_FullStore(code=s3_code, status=s3_status),
            bucket="b",
        )
    assert caught.value.full is False
    assert caught.value.s3_code == s3_code
    assert _outstanding(capacity_db) is None


def test_the_log_records_the_refusal_and_a_big_enough_write_clears_it(
    capacity_db,
) -> None:
    """Both directions, because a flag that never clears is a stuck alarm."""
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        artifact_storage.store_artifact(
            _BYTES, _SHA256, client=_FullStore(), bucket="b"
        )
    assert caught.value.full is True

    observation = _outstanding(capacity_db)
    assert observation is not None
    assert observation.s3_code == "XMinioStorageFull"
    assert observation.attempted_bytes == len(_BYTES)

    # A real write of the same size -- which is "at least the refused
    # size" -- clears it.
    artifact_storage.store_artifact(
        _BYTES, _SHA256, client=_HealthyStore(), bucket="b"
    )
    assert _outstanding(capacity_db) is None


# ---------------------------------------------------------------------------
# THE assertion. The entire design exists for this one.
# ---------------------------------------------------------------------------


def test_a_small_write_does_not_clear_a_larger_refusal(capacity_db) -> None:
    """A 1-byte success must NOT clear an 8 MiB refusal.

    This is the test that tells the correct implementation from the naive
    one, and without it the two are indistinguishable — to the suite and
    to a reviewer.

    It is not a hypothetical. Measured against MinIO
    ``RELEASE.2025-09-07T16-13-09Z`` on a volume filled to its free-space
    threshold, **the same store refused an 8 MiB write and accepted a
    1-byte write in the same second**: MinIO sizes its threshold check
    against the incoming object. An implementation that cleared on any
    successful write would therefore restore a green ``/status`` while
    every real ESS log upload still failed — a false negative in a health
    signal, which is worse than no signal because it is confidently
    wrong.

    The 1-byte write is still *recorded*. It is evidence of exactly the
    partial state an operator needs to see; it simply does not answer the
    refusal.
    """
    big = b"x" * (8 * 1024 * 1024)
    big_sha = hashlib.sha256(big).hexdigest()

    with capacity_db() as session:
        first_id = session.scalars(
            select(ArtifactStorageCapacityEvent.id)
            .order_by(ArtifactStorageCapacityEvent.id.desc())
            .limit(1)
        ).first() or 0

    with pytest.raises(artifact_storage.ArtifactStorageUnavailable):
        artifact_storage.store_artifact(
            big, big_sha, client=_FullStore(), bucket="b"
        )
    outstanding = _outstanding(capacity_db)
    assert outstanding is not None
    assert outstanding.attempted_bytes == len(big)

    # The store now accepts a tiny write -- exactly as the real one did.
    tiny = b"x"
    artifact_storage.store_artifact(
        tiny, hashlib.sha256(tiny).hexdigest(), client=_HealthyStore(), bucket="b"
    )

    still = _outstanding(capacity_db)
    assert still is not None, (
        "a 1-byte write cleared an 8 MiB refusal: the store that refuses "
        "real artifacts is being reported as healthy"
    )
    assert still.attempted_bytes == len(big)

    # ...and the small success was recorded rather than discarded, because
    # "small writes land, large ones do not" is the diagnosis.
    with capacity_db() as session:
        kinds = [
            (row.observation, row.observed_bytes)
            for row in session.scalars(
                select(ArtifactStorageCapacityEvent)
                .where(ArtifactStorageCapacityEvent.id > first_id)
                .order_by(ArtifactStorageCapacityEvent.id)
            ).all()
        ]
    assert kinds == [
        (capacity.ArtifactStorageCapacityObservation.refused, len(big)),
        (capacity.ArtifactStorageCapacityObservation.accepted, len(tiny)),
    ], kinds

    # Only a write that meets the refused size answers it.
    artifact_storage.store_artifact(
        big, big_sha, client=_HealthyStore(), bucket="b"
    )
    assert _outstanding(capacity_db) is None


def test_a_healthy_store_writes_no_rows_at_all(capacity_db) -> None:
    """The log is an incident log, not an upload log.

    Without this, ``note_successful_write`` could append on every upload
    and every other test here would still pass, while the table grew with
    traffic and the head-of-log query with it.
    """
    artifact_storage.store_artifact(
        _BYTES, _SHA256, client=_HealthyStore(), bucket="b"
    )
    with capacity_db() as session:
        assert session.scalars(select(ArtifactStorageCapacityEvent)).all() == []


def test_deduplicating_against_an_existing_object_does_not_clear_it(
    capacity_db,
) -> None:
    """Finding bytes already there is a read, and proves nothing about room.

    The distinction matters because content-addressed dedup is the common
    case on re-upload: if it cleared the record, one repeat upload would
    silence ``/status`` while the store was still full.
    """
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable):
        artifact_storage.store_artifact(
            _BYTES, _SHA256, client=_FullStore(), bucket="b"
        )
    assert _outstanding(capacity_db) is not None

    # A store that already holds these bytes: ``store_artifact`` returns on
    # the dedup path without ever calling ``put_object``.
    dedup = _HealthyStore({_KEY: _BYTES})
    artifact_storage.store_artifact(_BYTES, _SHA256, client=dedup, bucket="b")
    assert _outstanding(capacity_db) is not None


def test_a_refusal_of_unknown_size_is_not_cleared_by_any_write(
    capacity_db,
) -> None:
    """The server-side-copy arm may not know the object's size.

    It asks with a HEAD after the copy is refused (#545), and records no
    size when that fails. A refusal with no size cannot be answered by a size comparison, so no
    write may clear it — the conservative direction, and the one an
    operator can undo. Clearing it on any write would be the naive rule
    reintroduced through the one path that has no size to check.
    """
    capacity.record_refusal(
        s3_code="XMinioStorageFull",
        attempted_bytes=None,
        detail="copy_object refused",
        session_factory=capacity_db,
    )
    assert _outstanding(capacity_db) is not None

    huge = b"y" * (4 * 1024 * 1024)
    artifact_storage.store_artifact(
        huge, hashlib.sha256(huge).hexdigest(), client=_HealthyStore(), bucket="b"
    )
    assert _outstanding(capacity_db) is not None, (
        "a write cleared a refusal whose size was never known"
    )


@pytest.mark.parametrize(
    ("create_code", "expect_full"),
    [("XMinioStorageFull", True), ("AccessDenied", False)],
)
def test_a_refusing_create_bucket_leaves_as_the_typed_exception(
    create_code, expect_full, capacity_db
) -> None:
    """``create_bucket``'s own ``ClientError`` had nothing catching it.

    ``_ensure_bucket`` swallows a ``ClientError`` from ``head_bucket`` and
    then calls ``create_bucket``; only ``BotoCoreError`` was caught around
    that call, so a *raw* botocore exception escaped ``store_artifact`` and
    sailed past every ``except ArtifactStorageUnavailable`` downstream —
    the same gap, in the same function, that the ``BotoCoreError`` arm was
    added for. Invisible because most callers arrive through
    ``_store_and_record``, whose broad ``except`` relabelled anything;
    a direct caller such as archive restore did not.

    Both codes are asserted so the arm is shown to *classify* rather than
    merely to catch.
    """

    class _NoBucketAndRefusesToMakeOne:
        def head_bucket(self, **_kwargs):
            raise _client_error("NoSuchBucket", 404, "HeadBucket")

        def create_bucket(self, **_kwargs):
            raise _client_error(create_code, 507, "CreateBucket")

    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        artifact_storage.store_artifact(
            _BYTES, _SHA256, client=_NoBucketAndRefusesToMakeOne(), bucket="b"
        )
    assert caught.value.full is expect_full
    assert caught.value.s3_code == create_code


def test_a_full_store_refusing_a_server_side_copy_is_classified_too(
    capacity_db,
) -> None:
    """``copy_object`` is a write, and the reclaim sweep is what runs on a
    full store.

    Measured: MinIO answers ``XMinioStorageFull`` at 507 on ``copy_object``
    exactly as on ``put_object``. An operator reaching for the reclaim tool
    to free space is the worst audience for "storage unavailable, retry".
    """

    class _FullOnCopy(_HealthyStore):
        def copy_object(self, Bucket, Key, CopySource):
            raise _client_error("XMinioStorageFull", 507, "CopyObject")

    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        artifact_storage.hold_artifact_object(
            _SHA256, client=_FullOnCopy(), bucket="b"
        )
    assert caught.value.full is True


def test_the_typed_exception_survives_the_persistence_layer(
    monkeypatch, capacity_db
) -> None:
    """``_store_and_record`` must not re-wrap an already-classified failure.

    Its broad ``except Exception`` caught ``ArtifactStorageUnavailable`` and
    raised a *new* one with every discriminator back at its default, so a
    507 condition arrived at the handler as a bare 503. The API tests above
    would fail if this regressed, but they would not say why; this does.
    """
    from app.schemas.fragments.artifact import ArtifactIn
    from app.services.artifact_persistence import _decode_one, _store_and_record

    decoded = _decode_one(
        ArtifactIn(
            kind="ancillary",
            filename="note.txt",
            content_base64=base64.b64encode(_BYTES).decode("ascii"),
        )
    )

    class _Session:
        def add(self, _obj):
            raise AssertionError("no row may be added when the write failed")

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha: artifact_storage.store_artifact(
            content, sha, client=_FullStore(), bucket="b"
        ),
    )
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        _store_and_record(_Session(), 1, decoded, None)

    assert caught.value.full is True, "the classification was erased on the way out"


# ---------------------------------------------------------------------------
# The operator, who is who actually resolves a full disk
# ---------------------------------------------------------------------------


def test_an_operator_can_clear_a_refusal_explicitly(
    client, db_session, login_as, _api_admin_user
) -> None:
    """An operator is who frees space, so an operator can say it is freed.

    The route writes on the request transaction, so the refusal is placed
    there too -- one connection, one transaction, no cross-wiring.
    """
    login_as(_api_admin_user)
    capacity.append_observation(
        db_session,
        observation=capacity.ArtifactStorageCapacityObservation.refused,
        observed_bytes=8 * 1024 * 1024,
        s3_code="XMinioStorageFull",
        detail="scripted refusal",
    )
    assert capacity.current_full_state(db_session) is not None

    before = client.get("/api/v1/admin/artifact-storage/capacity")
    assert before.status_code == 200, before.text
    assert before.json()["storage_full"] is True
    assert before.json()["refused_bytes"] == 8 * 1024 * 1024

    response = client.post(
        "/api/v1/admin/artifact-storage/capacity/clear",
        json={"reason": "added a 2 TB disk and restarted MinIO"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["storage_full"] is False
    assert capacity.current_full_state(db_session) is None

    # It appended; it did not delete. The refusal is still the account of
    # what happened, and the operator's reason and identity are recorded.
    rows = db_session.scalars(
        select(ArtifactStorageCapacityEvent).order_by(ArtifactStorageCapacityEvent.id)
    ).all()
    assert [r.observation for r in rows] == [
        capacity.ArtifactStorageCapacityObservation.refused,
        capacity.ArtifactStorageCapacityObservation.operator_clear,
    ]
    assert rows[-1].detail == "added a 2 TB disk and restarted MinIO"
    assert rows[-1].created_by == _api_admin_user


def test_clearing_requires_a_reason(client, login_as, _api_admin_user) -> None:
    """The one clearing path resting on assertion has to say who said what."""
    login_as(_api_admin_user)
    response = client.post(
        "/api/v1/admin/artifact-storage/capacity/clear", json={"reason": ""}
    )
    assert response.status_code == 422, response.text


def test_a_non_admin_cannot_clear_it(client) -> None:
    """The default test user is an ordinary depositor."""
    response = client.post(
        "/api/v1/admin/artifact-storage/capacity/clear",
        json={"reason": "I would like the alarm to stop"},
    )
    assert response.status_code == 403, response.text


def test_the_operator_view_exposes_no_row_ids(
    client, db_session, login_as, _api_admin_user
) -> None:
    """DR-0028 Req. 2 applies to an operational report as much as an error."""
    login_as(_api_admin_user)
    event = capacity.append_observation(
        db_session,
        observation=capacity.ArtifactStorageCapacityObservation.refused,
        observed_bytes=4096,
        s3_code="XMinioStorageFull",
        detail="scripted refusal for sha=deadbeefdeadbeef",
    )
    body = client.get("/api/v1/admin/artifact-storage/capacity").json()
    assert set(body) == {
        "storage_full",
        "storage_full_observed_at",
        "s3_code",
        "refused_bytes",
    }
    # Neither the row id nor the store's prose (which carries a digest).
    assert str(event.id) not in str(body.get("s3_code"))
    assert "deadbeef" not in str(body)


# ---------------------------------------------------------------------------
# A free-space report, and the refusal it must not be allowed to answer
# ---------------------------------------------------------------------------


def test_a_capacity_report_clears_a_disk_full_refusal(capacity_db) -> None:
    """Recovery without waiting for a depositor to happen along.

    The number is the one MinIO's admin API reports. Measured on a 64 MiB
    scratch volume: ``availspace`` was 4,030,464 at the instant a
    4,194,304-byte write was refused, so the comparison is meaningful
    rather than nominal.
    """
    capacity.record_refusal(
        s3_code="XMinioStorageFull",
        attempted_bytes=4_194_304,
        detail="Storage backend has reached its minimum free drive threshold.",
        session_factory=capacity_db,
    )

    # Still short of the refused size: not clear.
    capacity.note_capacity_report(free_bytes=4_030_464, session_factory=capacity_db)
    assert _outstanding(capacity_db) is not None

    # Now genuinely roomy.
    capacity.note_capacity_report(
        free_bytes=64 * 1024 * 1024, session_factory=capacity_db
    )
    assert _outstanding(capacity_db) is None


def test_a_capacity_report_may_not_clear_a_bucket_quota_refusal(
    capacity_db,
) -> None:
    """Free disk says nothing about a quota, so it must not clear one.

    Measured against MinIO with ``mc quota set --size 20MiB``: a 2 MiB
    write was refused with ``XMinioAdminBucketQuotaExceeded`` while the
    admin API reported **437,858,304 bytes (418 MiB) free** on the drive.
    Letting free space answer that refusal would clear the flag on a store
    refusing every upload — the same false negative the small-write rule
    exists to prevent, arriving through a different door.
    """
    capacity.record_refusal(
        s3_code="XMinioAdminBucketQuotaExceeded",
        attempted_bytes=2_097_152,
        detail="Bucket quota exceeded",
        session_factory=capacity_db,
    )
    capacity.note_capacity_report(free_bytes=437_858_304, session_factory=capacity_db)

    assert _outstanding(capacity_db) is not None, (
        "418 MiB of free disk cleared a bucket-quota refusal"
    )

    # A real write of sufficient size still does clear it: the quota rule
    # narrows what may answer, it does not make the refusal permanent.
    capacity.note_successful_write(
        accepted_bytes=2_097_152, session_factory=capacity_db
    )
    assert _outstanding(capacity_db) is None


# ---------------------------------------------------------------------------
# SeaweedFS: a refusal that does not say "full", and a store that does (#545)
# ---------------------------------------------------------------------------
#
# SeaweedFS answers a full store with ``InternalError``/500, the same as any
# fault, so the code never classifies it. With ``S3_SEAWEEDFS_MASTER_URL``
# set, the write path asks the store for its room, and the answers below are
# the ones measured against 4.47 at the instant of a real refusal (see
# ``tests/services/test_artifact_storage_seaweedfs.py`` for the recordings).

_MASTER = "http://seaweedfs.test:9333"

#: Measured: 256 MiB store refused at 192 MiB written, slots exhausted, disk
#: still free.
_SLOTS_EXHAUSTED = seaweedfs.SeaweedCapacity(
    disk_free_bytes=66_736_128,
    disk_room_bytes=66_736_128,
    below_min_free=False,
    slot_room_bytes=0,
    volume_size_limit_bytes=64 * 1024 * 1024,
    free_slots=0,
    max_slots=3,
)
#: Measured: an empty 256 MiB store.
_ROOMY = seaweedfs.SeaweedCapacity(
    disk_free_bytes=268_120_064,
    disk_room_bytes=268_120_064,
    below_min_free=False,
    slot_room_bytes=3 * 64 * 1024 * 1024,
    volume_size_limit_bytes=64 * 1024 * 1024,
    free_slots=3,
    max_slots=3,
)


@pytest.fixture
def seaweed_answers(monkeypatch):
    """Point the write path at a SeaweedFS and script what it reports."""
    asked: list[dict] = []

    def _install(answer):
        monkeypatch.setattr(artifact_storage, "S3_SEAWEEDFS_MASTER_URL", _MASTER)

        def _report(**kwargs):
            asked.append(kwargs)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        monkeypatch.setattr(seaweedfs, "report_capacity", _report)
        return asked

    return _install


def _refused_internal_error(capacity_db):
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        artifact_storage.store_artifact(
            _BYTES,
            _SHA256,
            client=_FullStore(code="InternalError", status=500),
            bucket="b",
            session_factory=capacity_db,
        )
    return caught.value


def test_an_internal_error_on_a_seaweedfs_with_no_room_is_full(
    capacity_db, seaweed_answers
) -> None:
    asked = seaweed_answers(_SLOTS_EXHAUSTED)
    refused = _refused_internal_error(capacity_db)

    assert refused.full is True
    assert refused.s3_code == "InternalError"
    assert asked == [{"master_url": _MASTER, "bucket": artifact_storage.S3_BUCKET}]

    observation = _outstanding(capacity_db)
    assert observation is not None
    assert observation.s3_code == "InternalError"
    assert observation.attempted_bytes == len(_BYTES)
    # A free-space report can answer it later: it is not a quota refusal.
    assert observation.clearable_by_capacity_report is True

    with capacity_db() as session:
        detail = session.scalars(
            select(ArtifactStorageCapacityEvent.detail).order_by(
                ArtifactStorageCapacityEvent.id.desc()
            )
        ).first()
    # The operator reading the row sees what the store said, including the
    # slot count that disk free space alone would have hidden.
    assert "0 bytes of room" in detail, detail
    assert "disk free 66736128" in detail, detail
    assert "0/3 free" in detail, detail


def test_an_internal_error_on_a_seaweedfs_with_room_stays_unclassified(
    capacity_db, seaweed_answers
) -> None:
    """A transient fault on a store with room is not a full store."""
    asked = seaweed_answers(_ROOMY)
    refused = _refused_internal_error(capacity_db)

    assert len(asked) == 1, "the store was not asked"
    assert refused.full is False
    assert _outstanding(capacity_db) is None


def test_no_opinion_from_seaweedfs_leaves_the_refusal_unclassified(
    capacity_db, seaweed_answers
) -> None:
    seaweed_answers(None)
    refused = _refused_internal_error(capacity_db)
    assert refused.full is False
    assert _outstanding(capacity_db) is None


def test_a_capacity_check_that_raises_leaves_the_upload_failure_intact(
    capacity_db, seaweed_answers
) -> None:
    """``report_capacity`` promises never to raise; if it ever does, the
    depositor still gets the storage failure, not the probe's exception."""
    asked = seaweed_answers(RuntimeError("probe blew up"))
    refused = _refused_internal_error(capacity_db)

    assert len(asked) == 1
    assert isinstance(refused, artifact_storage.ArtifactStorageUnavailable)
    assert refused.full is False
    assert refused.s3_code == "InternalError"
    assert _outstanding(capacity_db) is None


def test_unset_asks_nothing(capacity_db, monkeypatch) -> None:
    def _must_not_be_called(**_kwargs):
        raise AssertionError("asked a SeaweedFS with S3_SEAWEEDFS_MASTER_URL unset")

    monkeypatch.setattr(seaweedfs, "report_capacity", _must_not_be_called)
    refused = _refused_internal_error(capacity_db)
    assert refused.full is False


def test_only_internal_error_earns_a_second_opinion(
    capacity_db, seaweed_answers
) -> None:
    """``AccessDenied`` on a full store is still a credentials problem."""
    asked = seaweed_answers(_SLOTS_EXHAUSTED)
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        artifact_storage.store_artifact(
            _BYTES,
            _SHA256,
            client=_FullStore(code="AccessDenied", status=403),
            bucket="b",
            session_factory=capacity_db,
        )
    assert caught.value.full is False
    assert asked == []


@pytest.fixture
def status_sees_only_seaweedfs(monkeypatch):
    """``/status`` with a bucket that answers and no MinIO admin opinion.

    A full SeaweedFS still answers ``HeadBucket`` (measured), and the MinIO
    admin probes must not reach whatever store the machine running the test
    happens to have on 9000.
    """
    monkeypatch.setattr(
        health,
        "_probe_bucket",
        lambda: {"healthy": True, "reachable": True, "reason": None},
    )
    monkeypatch.setattr(
        health.artifact_storage_admin, "report_free_bytes", lambda **_k: None
    )
    monkeypatch.setattr(
        health.artifact_storage_admin, "report_bucket_quota_bytes", lambda **_k: None
    )


def test_a_full_seaweedfs_reaches_the_depositor_as_507_and_status_as_degraded(
    client, use_store, capacity_db, seaweed_answers, status_sees_only_seaweedfs
) -> None:
    """The whole chain through the route, then ``/status``.

    Before #545 this upload answered 503 "retry later" and ``/status``
    stayed healthy.
    """
    seaweed_answers(_SLOTS_EXHAUSTED)
    use_store(_FullStore(code="InternalError", status=500))
    calc_id = _a_calculation(client)

    response = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts", json=_artifact_request()
    )
    assert response.status_code == 507, response.text
    assert response.json()["code"] == "artifact_storage_full"

    body = client.get("/api/v1/status").json()
    block = body["components"]["artifact_storage"]
    assert block["storage_full"] is True, block
    assert block["healthy"] is False, block
    assert "artifact_storage" in body["degraded"], body


def test_a_seaweedfs_report_of_room_clears_the_refusal_on_status(
    client, capacity_db, seaweed_answers, status_sees_only_seaweedfs
) -> None:
    """Recovery without waiting for a depositor: the operator frees space
    (here, the store now reports room) and the next poll goes green."""
    seaweed_answers(_SLOTS_EXHAUSTED)
    _refused_internal_error(capacity_db)

    still_full = client.get("/api/v1/status").json()
    assert still_full["components"]["artifact_storage"]["storage_full"] is True

    seaweed_answers(_ROOMY)
    recovered = client.get("/api/v1/status").json()
    assert recovered["components"]["artifact_storage"]["storage_full"] is False
    assert "artifact_storage" not in recovered["degraded"], recovered


#: Measured (fixture ``disk_full``): 3,833,856 bytes free on 128 MiB, of which
#: 2,491,678 are usable above weed mini's 1 % minimum; slots to spare.
_DISK_FULL = seaweedfs.SeaweedCapacity(
    disk_free_bytes=3_833_856,
    disk_room_bytes=2_491_678,
    below_min_free=False,
    slot_room_bytes=7 * 64 * 1024 * 1024,
    volume_size_limit_bytes=64 * 1024 * 1024,
    free_slots=7,
    max_slots=10,
)
_HELD_BYTES = 4 * 1024 * 1024


class _InternalErrorOnCopy(_HealthyStore):
    """Serves reads, including HEAD of the object being moved; refuses the copy."""

    def copy_object(self, Bucket, Key, CopySource):
        raise _client_error("InternalError", 500, "CopyObject")


@pytest.mark.parametrize(
    ("operation", "source_key"),
    [
        (artifact_storage.hold_artifact_object, _KEY),
        (artifact_storage.restore_held_object, artifact_storage.reclaim_hold_key(_SHA256)),
    ],
    ids=["hold", "restore"],
)
def test_a_refused_copy_on_a_full_disk_is_sized_and_classified(
    capacity_db, seaweed_answers, operation, source_key
) -> None:
    """The copy arm used to report no size, so on a full *disk* -- where the
    store still has some bytes -- "room < size" could not be asked and a
    full store read as a fault. The size is the object's, from a HEAD."""
    seaweed_answers(_DISK_FULL)
    store = _InternalErrorOnCopy({source_key: b"z" * _HELD_BYTES})
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        operation(_SHA256, client=store, bucket="b")
    assert caught.value.full is True
    observation = _outstanding(capacity_db)
    assert observation is not None
    assert observation.attempted_bytes == _HELD_BYTES


def test_a_refused_copy_whose_size_cannot_be_read_stays_unclassified(
    capacity_db, seaweed_answers
) -> None:
    """No HEAD answer, no size: only "no room at all" could explain it."""
    seaweed_answers(_DISK_FULL)
    store = _InternalErrorOnCopy({})  # HEAD of the source answers 404
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        artifact_storage.hold_artifact_object(_SHA256, client=store, bucket="b")
    assert caught.value.full is False
