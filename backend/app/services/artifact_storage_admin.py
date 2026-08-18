"""Ask a MinIO store how much room it has left, without writing anything.

Why this exists
---------------
The write path can only learn that the store is full by *being refused*,
which means recovery is only noticed when a depositor happens along with a
large enough upload. That could be days. This module asks the store
directly instead, so a full store that has been given more space goes
green on the next ``/status`` poll rather than on the next lucky upload.

What was measured, and what it costs
------------------------------------
The S3 API has no capacity query — that finding stands and is why the
write path remains the authority. MinIO, however, ships a *separate*
admin API, and it is reachable from this process:

* ``GET /minio/admin/v3/info``, SigV4-signed with the **same credentials
  the S3 client already uses**. In the standard deployment those are
  MinIO's root credentials (``docker-compose.yml`` sets
  ``MINIO_ROOT_USER``/``MINIO_ROOT_PASSWORD`` from ``S3_ACCESS_KEY``/
  ``S3_SECRET_KEY``), so no new secret and no deployment change is needed.
* The request **must** carry ``x-amz-content-sha256``; without it MinIO
  answers ``403 AccessDenied`` and the endpoint looks unauthorised when it
  is merely unsigned. :class:`~botocore.auth.S3SigV4Auth` adds it.
  Measured: bare :class:`~botocore.auth.SigV4Auth` → 403, ``S3SigV4Auth``
  → 200, same credentials, same second.
* The response reports per-drive ``totalspace``/``usedspace``/
  ``availspace``, and the number genuinely predicts refusal. Measured on a
  64 MiB scratch volume: at the instant MinIO answered
  ``XMinioStorageFull`` to a **4,194,304**-byte write, ``availspace`` was
  **4,030,464** — just below it.

Two limits, both measured, both load-bearing
--------------------------------------------
**It cannot see a bucket quota.** With ``mc quota set --size 20MiB``,
MinIO refused a 2 MiB write with ``XMinioAdminBucketQuotaExceeded`` while
this endpoint reported **437,858,304 bytes (418 MiB) free**. A capacity
report must therefore never be allowed to answer a quota refusal, or it
would clear the flag on a store that refuses every upload. That
restriction lives in
:attr:`~app.services.artifact_storage_capacity.StorageFullState.clearable_by_capacity_report`.

**It is MinIO-specific.** AWS S3 has no such endpoint and no notion of
"full". Every failure here — a non-MinIO store, an admin API that
answers 404, a credential without ``admin:ServerInfo``, a timeout, an
unparseable body — returns ``None``, meaning *no opinion*. ``None`` never
degrades ``/status`` and never clears a refusal; the write path's own
evidence is untouched by this module being unavailable. A capacity probe
that could turn an S3 deployment red would be a worse bug than the one it
is here to fix.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urlsplit

from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

logger = logging.getLogger(__name__)

#: MinIO pins its admin API version and answers ``426
#: XMinioAdminVersionMismatch`` to anything else — measured against
#: ``v4``. Hard-coded rather than negotiated: a mismatch is a "no opinion"
#: like any other failure, and version negotiation would be machinery in
#: aid of a signal that is explicitly supplementary.
_ADMIN_INFO_PATH = "/minio/admin/v3/info"

#: Short, and it has to be. This runs **inline in the ``/status`` request
#: thread**, after the bucket probe's own 4s deadline, so it is additive:
#: the worst case an operator's poll can cost is the two together. A store
#: slow to answer its own admin API is one ``/status`` should stop waiting
#: for rather than one it should report on — the answer is only ever used
#: to *clear* a refusal, so giving up costs nothing but a slower recovery.
#: Pinned by a test alongside the bucket probe's deadline.
_TIMEOUT_SECONDS = 2.0


def report_free_bytes(
    *,
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    region: str = "us-east-1",
    timeout: float = _TIMEOUT_SECONDS,
) -> Optional[int]:
    """Free bytes the store reports across its drives, or ``None``.

    ``None`` means *no opinion* and is the answer for every failure mode,
    including "this is not a MinIO". Never raises.
    """
    if not endpoint_url:
        return None
    url = endpoint_url.rstrip("/") + _ADMIN_INFO_PATH
    try:
        request = AWSRequest(method="GET", url=url, data=b"")
        S3SigV4Auth(Credentials(access_key, secret_key), "s3", region).add_auth(request)
        opened = urllib.request.urlopen(
            urllib.request.Request(url, method="GET", headers=dict(request.headers)),
            timeout=timeout,
        )
        with opened as response:
            info = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # 403 without admin rights, 404 on a non-MinIO, 426 on a version
        # mismatch. All the same answer: this store will not tell us.
        logger.debug(
            "storage capacity probe: %s answered HTTP %s; no capacity opinion",
            _sanitized(endpoint_url),
            exc.code,
        )
        return None
    except Exception as exc:
        logger.debug(
            "storage capacity probe: %s did not answer (%s); no capacity opinion",
            _sanitized(endpoint_url),
            type(exc).__name__,
        )
        return None

    try:
        drives = [
            drive
            for server in info.get("servers") or []
            for drive in server.get("drives") or []
        ]
        # A drive reporting no ``availspace`` key contributes nothing
        # rather than zero: treating "did not say" as "no room" would
        # invent a full store out of an unfamiliar response shape.
        available = [
            drive["availspace"]
            for drive in drives
            if isinstance(drive.get("availspace"), int)
        ]
        if not available:
            return None
        # Sum across drives, which is the same aggregate MinIO's own
        # threshold is enforced against per drive. On the single-drive
        # deployments TCKDB runs, sum and min coincide.
        return sum(available)
    except Exception:
        logger.debug("storage capacity probe: unfamiliar response shape")
        return None


def _sanitized(endpoint_url: str) -> str:
    """``scheme://host:port``, so a credential in the URL cannot be logged."""
    try:
        parts = urlsplit(endpoint_url)
        if parts.scheme and parts.hostname:
            port = f":{parts.port}" if parts.port else ""
            return f"{parts.scheme}://{parts.hostname}{port}"
    except ValueError:
        pass
    return "(unparseable endpoint)"


__all__ = ["report_free_bytes"]
