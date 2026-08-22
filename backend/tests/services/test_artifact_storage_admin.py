"""Asking MinIO how much room it has, and never being hurt by the answer.

What this module is for
-----------------------
The write path can only learn the store is full by being refused, so it
only learns the store *recovered* when a depositor happens along with a
big enough upload. MinIO's admin API answers the question directly, with
the credentials the S3 client already holds, and costs no writes.

What was measured, against MinIO on a scratch container
--------------------------------------------------------
* ``GET /minio/admin/v3/info`` signed with :class:`botocore.auth.SigV4Auth`
  answers **403 AccessDenied**; signed with
  :class:`botocore.auth.S3SigV4Auth` — which adds ``x-amz-content-sha256``
  — it answers **200**, with the same credentials in the same second. The
  endpoint is not privileged-by-default; it is picky about signing, and
  the failure looks exactly like an authorization problem.
* ``availspace`` predicts refusal: on a 64 MiB volume it read **4,030,464**
  at the instant MinIO answered ``XMinioStorageFull`` to a **4,194,304**-byte
  write.
* It is blind to bucket quotas: with a 20 MiB quota set, a 2 MiB write was
  refused while this endpoint reported **437,858,304** bytes free. That is
  why a capacity report may not answer a quota refusal, asserted in
  ``tests/api/test_api_artifact_storage_capacity.py``.

The quota probe, which answers the question free space cannot
--------------------------------------------------------------
``GET /minio/admin/v3/get-bucket-quota?bucket=<name>`` answers **200**
with ``{"quota":20971520,"size":0,"rate":0,"requests":0,
"quotatype":"hard"}``. Three measured facts drive the tests below:

* ``size`` **is not usage**. It read ``0`` for the whole run, including
  while the bucket held 60 MiB, so nothing here reads it.
* No quota configured answers ``200 {"quota":0,...}`` with ``quotatype``
  **absent** rather than empty. ``quota == 0`` is *no opinion*, not a
  quota of zero bytes — the difference between saying nothing about a
  default deployment and declaring it unable to hold a single object.
* A missing bucket answers ``404 NoSuchBucket``.

It also needs **less** privilege than ``report_free_bytes``: a policy of
exactly ``admin:GetBucketQuota`` answers it and is *denied*
``/minio/admin/v3/info``. Nothing here requires MinIO root.

Why the failure modes get as many tests as the success
-------------------------------------------------------
This probe is supplementary. The authoritative signal is the write path's
own refusal, and a probe that could *damage* that by failing would be a
worse bug than the one it was added for. So every failure returns ``None``
— "no opinion" — and ``None`` never clears a refusal and never degrades
``/status``. Against AWS S3, which has no such endpoint and no notion of
"full", ``None`` is the permanent and correct answer.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from app.services import artifact_storage_admin as admin

ENDPOINT = "http://minio.test:9000"


def _info(drives: list[dict]) -> bytes:
    return json.dumps({"servers": [{"endpoint": "minio.test:9000", "drives": drives}]}).encode()


@pytest.fixture
def answer(monkeypatch):
    """Make the admin endpoint answer with a scripted body or exception."""

    def _install(effect):
        captured: dict = {}

        def _urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            if isinstance(effect, BaseException):
                raise effect
            return io.BytesIO(effect)

        monkeypatch.setattr(admin.urllib.request, "urlopen", _urlopen)
        return captured

    return _install


def _call() -> int | None:
    return admin.report_free_bytes(
        endpoint_url=ENDPOINT, access_key="ak", secret_key="sk"
    )


def test_it_sums_free_space_across_drives(answer) -> None:
    answer(_info([{"availspace": 1000}, {"availspace": 2500}]))
    assert _call() == 3500


def test_it_signs_the_request_with_a_content_hash(answer) -> None:
    """The header whose absence makes MinIO answer 403.

    Asserted because the failure it prevents is indistinguishable from a
    permissions problem, and someone re-deriving it loses an afternoon —
    this test is where that afternoon is banked.
    """
    captured = answer(_info([{"availspace": 1}]))
    _call()

    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert "x-amz-content-sha256" in headers, headers
    assert headers["authorization"].startswith("AWS4-HMAC-SHA256 "), headers
    assert captured["url"] == f"{ENDPOINT}/minio/admin/v3/info"


@pytest.mark.parametrize(
    "effect",
    [
        # A non-MinIO store, or one behind a proxy that does not route this.
        urllib.error.HTTPError(ENDPOINT, 404, "Not Found", {}, io.BytesIO(b"")),
        # A credential without admin rights, or an unsigned request.
        urllib.error.HTTPError(ENDPOINT, 403, "Forbidden", {}, io.BytesIO(b"")),
        # A MinIO that has moved its admin API version on.
        urllib.error.HTTPError(ENDPOINT, 426, "Upgrade", {}, io.BytesIO(b"")),
        # The store is simply not there.
        OSError("connection refused"),
        TimeoutError("timed out"),
    ],
)
def test_every_failure_is_no_opinion_and_never_raises(answer, effect) -> None:
    """``None``, not an exception and not a zero.

    Zero would read as "no room" and would degrade ``/status`` on an AWS S3
    deployment forever. An exception would propagate into a health endpoint
    whose entire job is to answer when things are broken.
    """
    answer(effect)
    assert _call() is None


@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        b"{}",
        json.dumps({"servers": []}).encode(),
        # Present but shaped differently: no capacity numbers at all.
        _info([{"endpoint": "/data", "state": "ok"}]),
        # A string where an int belongs.
        _info([{"availspace": "lots"}]),
    ],
)
def test_an_unfamiliar_response_is_no_opinion(answer, body) -> None:
    """"Did not say" must never be read as "said zero".

    Treating a missing or non-integer ``availspace`` as 0 would invent a
    full store out of an unfamiliar response shape.
    """
    answer(body)
    assert _call() is None


def test_a_drive_that_did_not_say_contributes_nothing(answer) -> None:
    """A partial answer is still an answer, for the drives that gave one."""
    answer(_info([{"availspace": 4096}, {"state": "offline"}]))
    assert _call() == 4096


def test_an_empty_endpoint_is_not_probed(monkeypatch) -> None:
    """No endpoint configured is not a reason to construct a request."""

    def _boom(*_a, **_k):
        raise AssertionError("no HTTP call may be made without an endpoint")

    monkeypatch.setattr(admin.urllib.request, "urlopen", _boom)
    assert (
        admin.report_free_bytes(endpoint_url="", access_key="ak", secret_key="sk")
        is None
    )


def test_the_sanitizer_keeps_a_credential_out_of_the_log() -> None:
    """The endpoint is logged on failure; a URL can carry a password."""
    assert (
        admin._sanitized("http://user:hunter2@minio.test:9000/x")
        == "http://minio.test:9000"
    )


# ---------------------------------------------------------------------------
# The bucket quota probe
# ---------------------------------------------------------------------------

BUCKET = "tckdb-artifacts"


def _quota() -> int | None:
    return admin.report_bucket_quota_bytes(
        endpoint_url=ENDPOINT, bucket=BUCKET, access_key="ak", secret_key="sk"
    )


def test_it_reports_a_configured_hard_quota(answer) -> None:
    """The measured body, verbatim, including the field that lies."""
    answer(
        json.dumps(
            {
                "quota": 20971520,
                "size": 0,
                "rate": 0,
                "requests": 0,
                "quotatype": "hard",
            }
        ).encode()
    )
    assert _quota() == 20971520


def test_the_size_field_is_never_read_as_usage(answer) -> None:
    """The trap, pinned.

    ``size`` read **0** throughout a run in which the bucket held 60 MiB.
    A caller that took it for usage would compute a headroom equal to the
    whole quota on a bucket that was three times over it. This asserts the
    only defence that survives refactoring: the returned value depends on
    ``quota`` alone, so a wildly wrong ``size`` cannot move it.
    """
    honest = json.dumps({"quota": 20971520, "size": 0, "quotatype": "hard"}).encode()
    absurd = json.dumps(
        {"quota": 20971520, "size": 62914560, "quotatype": "hard"}
    ).encode()
    answer(honest)
    first = _quota()
    answer(absurd)
    assert _quota() == first == 20971520


def test_no_quota_configured_is_no_opinion(answer) -> None:
    """Measured: ``{"quota":0,...}`` with ``quotatype`` absent, not empty.

    Returning ``0`` here would be the expensive kind of wrong: every
    deployment without a bucket quota — which is the default, and is the
    Pi — would report zero headroom and warn forever, and an alert that is
    always on is an alert that is off.
    """
    answer(json.dumps({"quota": 0, "size": 0, "rate": 0, "requests": 0}).encode())
    assert _quota() is None


def test_it_signs_the_quota_request_and_names_the_bucket(answer) -> None:
    """Same ``x-amz-content-sha256`` requirement, and the bucket in the query.

    The bucket travels as a query parameter, which SigV4 signs as part of
    the canonical request: a URL built for the signer and a different one
    handed to the opener would produce a 403 that looks like a
    permissions problem. Asserting they are the same string is what stops
    that afternoon happening twice.
    """
    captured = answer(json.dumps({"quota": 1024, "quotatype": "hard"}).encode())
    _quota()

    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert "x-amz-content-sha256" in headers, headers
    assert headers["authorization"].startswith("AWS4-HMAC-SHA256 "), headers
    assert (
        captured["url"]
        == f"{ENDPOINT}/minio/admin/v3/get-bucket-quota?bucket={BUCKET}"
    )


@pytest.mark.parametrize(
    "effect",
    [
        # Measured: a bucket that is not there.
        urllib.error.HTTPError(ENDPOINT, 404, "NoSuchBucket", {}, io.BytesIO(b"")),
        # A credential without ``admin:GetBucketQuota``.
        urllib.error.HTTPError(ENDPOINT, 403, "Forbidden", {}, io.BytesIO(b"")),
        urllib.error.HTTPError(ENDPOINT, 426, "Upgrade", {}, io.BytesIO(b"")),
        OSError("connection refused"),
        TimeoutError("timed out"),
    ],
)
def test_every_quota_failure_is_no_opinion_and_never_raises(answer, effect) -> None:
    answer(effect)
    assert _quota() is None


@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        b"[]",
        b"{}",
        # A string where an int belongs.
        json.dumps({"quota": "20MiB"}).encode(),
        # ``bool`` is an ``int`` in Python; ``True`` must not become a
        # one-byte quota that reports every store as full.
        json.dumps({"quota": True}).encode(),
        # Negative is not a quota.
        json.dumps({"quota": -1}).encode(),
    ],
)
def test_an_unfamiliar_quota_response_is_no_opinion(answer, body) -> None:
    answer(body)
    assert _quota() is None


@pytest.mark.parametrize("endpoint,bucket", [("", BUCKET), (ENDPOINT, "")])
def test_the_quota_probe_needs_both_an_endpoint_and_a_bucket(
    monkeypatch, endpoint, bucket
) -> None:
    def _boom(*_a, **_k):
        raise AssertionError("no HTTP call may be made without endpoint and bucket")

    monkeypatch.setattr(admin.urllib.request, "urlopen", _boom)
    assert (
        admin.report_bucket_quota_bytes(
            endpoint_url=endpoint, bucket=bucket, access_key="ak", secret_key="sk"
        )
        is None
    )
