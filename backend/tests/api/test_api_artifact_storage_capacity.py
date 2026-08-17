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

from app.services import artifact_storage

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


@pytest.fixture(autouse=True)
def _no_leftover_storage_full_latch():
    """The latch is process state; no test may inherit another's."""
    artifact_storage.clear_storage_full_observation()
    yield
    artifact_storage.clear_storage_full_observation()


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


def test_a_refused_write_that_is_not_about_space_stays_unclassified() -> None:
    """``AccessDenied`` is a real outage-shaped failure, not a full store.

    Without this, ``_STORAGE_FULL_CODES`` could be widened to "any
    ``ClientError`` on a write" and every test above would still pass,
    which would turn a credentials problem into "free some space".
    """
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        artifact_storage.store_artifact(
            _BYTES, _SHA256, client=_FullStore(code="AccessDenied", status=403),
            bucket="b",
        )
    assert caught.value.full is False
    assert caught.value.s3_code == "AccessDenied"
    assert artifact_storage.last_storage_full_observation() is None


def test_the_latch_records_the_refusal_and_a_successful_write_clears_it() -> None:
    """Both directions, because a latch that never clears is a stuck alarm."""
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable) as caught:
        artifact_storage.store_artifact(
            _BYTES, _SHA256, client=_FullStore(), bucket="b"
        )
    assert caught.value.full is True

    observation = artifact_storage.last_storage_full_observation()
    assert observation is not None
    assert observation.s3_code == "XMinioStorageFull"
    assert observation.attempted_bytes == len(_BYTES)

    # A real write clears it.
    artifact_storage.store_artifact(
        _BYTES, _SHA256, client=_HealthyStore(), bucket="b"
    )
    assert artifact_storage.last_storage_full_observation() is None


def test_deduplicating_against_an_existing_object_does_not_clear_the_latch() -> None:
    """Finding bytes already there is a read, and proves nothing about room.

    The distinction matters because content-addressed dedup is the common
    case on re-upload: if it cleared the latch, one repeat upload would
    silence ``/status`` while the store was still full.
    """
    with pytest.raises(artifact_storage.ArtifactStorageUnavailable):
        artifact_storage.store_artifact(
            _BYTES, _SHA256, client=_FullStore(), bucket="b"
        )
    assert artifact_storage.last_storage_full_observation() is not None

    # A store that already holds these bytes: ``store_artifact`` returns on
    # the dedup path without ever calling ``put_object``.
    dedup = _HealthyStore({_KEY: _BYTES})
    artifact_storage.store_artifact(_BYTES, _SHA256, client=dedup, bucket="b")
    assert artifact_storage.last_storage_full_observation() is not None


@pytest.mark.parametrize(
    ("create_code", "expect_full"),
    [("XMinioStorageFull", True), ("AccessDenied", False)],
)
def test_a_refusing_create_bucket_leaves_as_the_typed_exception(
    create_code, expect_full
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


def test_a_full_store_refusing_a_server_side_copy_is_classified_too() -> None:
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


def test_the_typed_exception_survives_the_persistence_layer(monkeypatch) -> None:
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
