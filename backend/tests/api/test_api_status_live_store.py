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
  probe's contract turns every HTTP error into "no opinion", so headroom
  is ``None`` and ``/status`` raises no warning. That is the correct
  answer and the one asserted here: a store TCKDB cannot ask about
  capacity must read as silent, never as low and never as broken.
* ``HeadBucket`` answers 200 on an existing bucket, so the storage
  component is healthy.
* With keys configured an unsigned request is refused (``AccessDenied``,
  403); started without keys, SeaweedFS accepts it.

Off CI these skip when no store answers; on CI they fail, because CI
always provides one (see :mod:`tests.services._live_object_store`).
"""

from __future__ import annotations

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from sqlalchemy.orm import sessionmaker

from app.api.routes import health
from app.services import artifact_storage, artifact_storage_admin, artifact_storage_headroom
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


def test_seaweedfs_gives_the_headroom_probe_no_opinion(live_store) -> None:
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
    assert (
        artifact_storage_headroom.current_headroom(
            bucket=artifact_storage.S3_BUCKET, **credentials
        )
        is None
    )
