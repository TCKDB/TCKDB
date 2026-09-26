"""``/status`` and the headroom probe against the real object store.

Every other storage test in ``test_api_status.py`` scripts the store's
answer, which is right for pinning TCKDB's side of each condition and says
nothing about whether a real server gives those answers. These tests ask
the store the suite is actually configured for -- on CI, the SeaweedFS
service container that replaced MinIO as the default store TCKDB ships
(#541).

What was measured against SeaweedFS 4.47 before these were written:

* The MinIO admin paths the headroom probe uses
  (``/minio/admin/v3/info``, ``/minio/admin/v3/get-bucket-quota``) answer
  **404 NoSuchBucket**: SeaweedFS reads ``minio`` as a bucket name. The
  probe's contract turns every HTTP error into "no opinion". That is the
  correct answer and the one asserted here: a store TCKDB cannot ask about
  capacity must read as silent, never as low and never as broken. Since
  #545 SeaweedFS is asked in its own terms instead, through
  ``S3_SEAWEEDFS_MASTER_URL``; the last tests here read that signal from
  the real store and prove the store's internal ports are locked down.
* ``HeadBucket`` answers 200 on an existing bucket, so the storage
  component is healthy.
* With keys configured an unsigned request is refused (``AccessDenied``,
  403); started without keys, SeaweedFS accepts it.

Off CI these skip when no store answers; on CI they fail, because CI
always provides one (see :mod:`tests.services._live_object_store`).
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from sqlalchemy.orm import sessionmaker

from app.api.routes import health
from app.services import (
    artifact_storage,
    artifact_storage_admin,
    artifact_storage_headroom,
    artifact_storage_seaweedfs,
)
from tests.services._live_object_store import (
    on_ci,
    store_description,
    store_server_header,
    unavailable,
)


@pytest.fixture(autouse=True)
def _bind_status_probes_to_test_database(db_engine, monkeypatch):
    """Same baseline as ``test_api_status.py``: probe the pytest database."""
    monkeypatch.setattr(
        health, "SessionLocal", sessionmaker(bind=db_engine, expire_on_commit=False)
    )


@pytest.fixture
def live_store():
    """The configured store's ``Server`` header, once its bucket answers."""
    server = store_server_header(bucket=True)
    if server is None:
        unavailable(f"{store_description()} did not answer HeadBucket")
    artifact_storage_headroom.clear_quota_cache()
    yield server
    artifact_storage_headroom.clear_quota_cache()


def test_status_reports_the_configured_store_healthy(client, live_store) -> None:
    body = client.get("/api/v1/status").json()
    block = body["components"]["artifact_storage"]

    # It probed the store this suite is pointed at, not a stand-in.
    assert block["endpoint"] == health._sanitized_endpoint(
        artifact_storage.S3_ENDPOINT_URL
    ), block
    assert block["bucket"] == artifact_storage.S3_BUCKET, block

    assert block["healthy"] is True, block
    assert block["reachable"] is True, block
    assert block["reason"] is None, block
    assert block["storage_full"] is False, block
    # No capacity opinion must mean no warning, not a warning about zero.
    assert block["warnings"] == [], block
    assert "artifact_storage" not in body["degraded"], body


def test_the_store_refuses_an_unsigned_request(live_store) -> None:
    """Authentication is actually on, not merely configured.

    Measured against SeaweedFS 4.47: started without
    ``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY`` it accepts every
    request, signed with any key or not signed at all. Every other test
    here signs its requests, so all of them pass against such an open
    store; this one does not. It is what goes red if a future image stops
    reading the credentials from its environment.
    """
    anonymous = boto3.client(
        "s3",
        endpoint_url=artifact_storage.S3_ENDPOINT_URL,
        region_name=artifact_storage.S3_REGION,
        config=BotoConfig(
            signature_version=UNSIGNED,
            connect_timeout=2,
            read_timeout=2,
            retries={"max_attempts": 0, "mode": "legacy"},
        ),
    )
    with pytest.raises(ClientError) as refused:
        anonymous.list_buckets()
    status = refused.value.response["ResponseMetadata"]["HTTPStatusCode"]
    assert status == 403, refused.value.response


def test_seaweedfs_gives_the_minio_admin_probes_no_opinion(live_store) -> None:
    if not live_store.startswith("SeaweedFS"):
        message = (
            f"{store_description()} identifies as {live_store!r}, not SeaweedFS; "
            "this pins SeaweedFS's answer to the MinIO admin API"
        )
        if on_ci():
            # CI's store is SeaweedFS. If that changes, revisit this test on
            # purpose rather than letting it skip.
            pytest.fail(message)
        pytest.skip(message)

    credentials = {
        "endpoint_url": artifact_storage.S3_ENDPOINT_URL,
        "access_key": artifact_storage.S3_ACCESS_KEY,
        "secret_key": artifact_storage.S3_SECRET_KEY,
    }
    assert artifact_storage_admin.report_free_bytes(**credentials) is None
    assert (
        artifact_storage_admin.report_bucket_quota_bytes(
            bucket=artifact_storage.S3_BUCKET, **credentials
        )
        is None
    )
    # Without ``seaweedfs_master_url`` only the MinIO arms are consulted,
    # and against SeaweedFS both are silent.
    assert (
        artifact_storage_headroom.current_headroom(
            bucket=artifact_storage.S3_BUCKET, **credentials
        )
        is None
    )


# ---------------------------------------------------------------------------
# SeaweedFS: the capacity signal, and the side door that must stay shut (#545)
# ---------------------------------------------------------------------------
#
# ``weed mini`` also runs a filer (8888), a master (9333) and a volume server
# (9340). Measured on 4.47 with the pre-#545 configuration, all three
# answered anyone on the network with no credentials: objects were read and
# written through the filer, needles through the volume server, and the
# master grew volumes and would delete collections on request. The fix is
# configuration -- JWT signing on the filer and volume server, and a guard
# whitelist on the master and volume server admin API (see the ``seaweedfs``
# service in docker-compose.yml and the CI workflows) -- so these tests are
# what goes red if that configuration is lost. They reach the ports the way
# another container on the network would: without credentials, from an
# address that is not the store's own loopback.


@pytest.fixture
def seaweedfs_master(live_store) -> str:
    """``S3_SEAWEEDFS_MASTER_URL``, required on CI."""
    if not live_store.startswith("SeaweedFS"):
        message = f"{store_description()} is {live_store!r}, not SeaweedFS"
        if on_ci():
            pytest.fail(message)
        pytest.skip(message)
    master = os.environ.get("S3_SEAWEEDFS_MASTER_URL", "")
    if not master:
        unavailable("S3_SEAWEEDFS_MASTER_URL is not set")
    return master.rstrip("/")


def _sibling(master: str, port: int) -> str:
    """Another ``weed mini`` port on the master's host."""
    parts = urlsplit(master)
    return f"{parts.scheme}://{parts.hostname}:{port}"


def _status_of(method: str, url: str, body: bytes | None = None) -> int:
    request = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_the_seaweedfs_capacity_signal_is_readable(seaweedfs_master) -> None:
    """Requirement (c): the status pages still answer the API.

    Also pins the master status page's shape against the image CI pins: a
    digest bump that changes it turns this red instead of silencing the
    full-store detection.
    """
    capacity = artifact_storage_seaweedfs.report_capacity(
        master_url=seaweedfs_master, bucket=artifact_storage.S3_BUCKET
    )
    assert capacity is not None, "SeaweedFS gave no capacity opinion"
    assert capacity.volume_size_limit_bytes >= 1024 * 1024, capacity
    assert capacity.max_slots >= 1, capacity
    assert capacity.disk_free_bytes > 0, capacity
    assert capacity.room_bytes > 0, capacity

    # And the headroom probe uses it.
    report = artifact_storage_headroom.current_headroom(
        endpoint_url=artifact_storage.S3_ENDPOINT_URL,
        bucket=artifact_storage.S3_BUCKET,
        access_key=artifact_storage.S3_ACCESS_KEY,
        secret_key=artifact_storage.S3_SECRET_KEY,
        seaweedfs_master_url=seaweedfs_master,
    )
    assert report is not None
    assert report.volume_slot_bytes == capacity.slot_room_bytes


def test_the_filer_refuses_unauthenticated_reads_and_writes(seaweedfs_master) -> None:
    """Requirement (a), filer half. Before #545 these answered 201 and 200."""
    filer = _sibling(seaweedfs_master, 8888)
    bucket = artifact_storage.S3_BUCKET

    # A real object, written the authenticated way, so the read below is
    # refused for want of credentials and not because nothing is there.
    key = f"sidedoor-probe/{uuid.uuid4().hex}"
    signed = artifact_storage._get_s3_client()
    signed.put_object(Bucket=bucket, Key=key, Body=b"written through S3")
    try:
        assert signed.get_object(Bucket=bucket, Key=key)["Body"].read() == b"written through S3"

        assert _status_of("GET", f"{filer}/buckets/{bucket}/{key}") == 401
        assert _status_of("GET", f"{filer}/buckets/{bucket}/") == 401
        written = f"{filer}/buckets/{bucket}/sidedoor-probe/{uuid.uuid4().hex}"
        assert _status_of("PUT", written, b"unauthenticated") == 401
    finally:
        signed.delete_object(Bucket=bucket, Key=key)


def test_the_volume_server_refuses_unauthenticated_reads_and_writes(
    seaweedfs_master,
) -> None:
    """Requirement (a), volume half: a needle can be neither read nor written."""
    volume = _sibling(seaweedfs_master, artifact_storage_seaweedfs.VOLUME_SERVER_PORT)
    needle = f"{volume}/1,01{uuid.uuid4().hex[:8]}"
    assert _status_of("GET", needle) == 401
    assert _status_of("POST", needle, b"unauthenticated") == 401


def test_the_master_refuses_admin_requests_from_elsewhere(seaweedfs_master) -> None:
    """Requirement (d). ``/dir/assign`` is here too: it hands out the JWT a
    volume server write needs, so an open assign is an open volume server."""
    for path in (
        "/vol/grow?count=1",
        "/col/delete?collection=sidedoor-probe",
        "/dir/assign",
        "/dir/lookup?volumeId=1",
    ):
        assert _status_of("GET", f"{seaweedfs_master}{path}") == 401, path
