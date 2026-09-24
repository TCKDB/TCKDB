"""Reach the object store the suite is configured for, or say why not.

A handful of tests talk to a real S3-compatible server rather than a
scripted fake: they are the only place the write path, the ``/status``
probe and the headroom probe meet an actual store. Off CI, a missing
store is a skip, so a developer without one can still run the suite.

**On CI a missing store is a failure.** The workflows start one, so not
reaching it means the service container or the ``S3_*`` settings are
wrong, and a skip would turn exactly that into a green gate that tested
nothing. Before this helper, the real-store tests skipped with "MinIO not
running" whenever the store could not be listed, on CI as well: had the
object store service been swapped for one the tests could not reach, every
one of them would have skipped and every gate would have passed.
"""

from __future__ import annotations

import os

import pytest
from botocore.config import Config as BotoConfig

from app.services import artifact_storage


def on_ci() -> bool:
    """True on a GitHub Actions runner, which sets this for every step."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def unavailable(reason: str) -> None:
    """Skip locally; fail on CI, where a store is always provided."""
    if on_ci():
        pytest.fail(f"{reason} -- CI starts an object store, so this is a broken setup, not a skip")
    pytest.skip(reason)


def _probe_client():
    # Short timeouts and no retries: a dead endpoint should cost seconds,
    # not the default retry budget, since this can run at collection.
    return artifact_storage._get_s3_client(
        config=BotoConfig(
            connect_timeout=2,
            read_timeout=2,
            retries={"max_attempts": 0, "mode": "legacy"},
        )
    )


def store_server_header(*, bucket: bool = False) -> str | None:
    """The store's ``Server`` header, or ``None`` if it did not answer.

    With ``bucket=False`` this lists buckets, which needs only a reachable
    store and valid credentials -- enough for tests that create buckets of
    their own. With ``bucket=True`` it ``head_bucket``\\ s the configured
    ``S3_BUCKET``, which CI creates before the gates run and which the
    ``/status`` probe checks.
    """
    client = _probe_client()
    try:
        if bucket:
            response = client.head_bucket(Bucket=artifact_storage.S3_BUCKET)
        else:
            response = client.list_buckets()
    except Exception:
        return None
    return response["ResponseMetadata"]["HTTPHeaders"].get("server", "")


def store_description() -> str:
    """Where the suite was pointed, for a skip or failure message."""
    return (
        f"object store {artifact_storage.S3_ENDPOINT_URL} "
        f"bucket {artifact_storage.S3_BUCKET!r}"
    )
