"""Health and readiness endpoints.

``/health`` is a lightweight liveness probe: it confirms the process is
up and can reach the database with a trivial ``SELECT 1``.

``/readyz`` is the readiness probe used by operators and load balancers
to decide whether to route traffic. In addition to DB connectivity it
reports the Alembic schema revision currently installed, so a deploy
that comes up against a partially-migrated database is visible without
needing to shell into the box. The DB statement-timeout configured on
every connection (see :mod:`app.api.deps`) bounds the readiness probe
so it cannot hang on a wedged session.

Both endpoints intentionally return a tiny stable JSON shape and never
leak driver text, hostnames, or credentials.

``/status`` is the richer, human-facing summary. It reports every hard
dependency, because a health endpoint that omits one is worse than having
no check for it: it turns an outage into a confident all-clear. That is
not hypothetical here -- see :func:`_artifact_storage_status`.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from urllib.parse import urlsplit

from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionLocal
from app.api.startup_checks import server_encoding
from app.services import artifact_storage

router = APIRouter()
logger = logging.getLogger(__name__)

#: Name given to the inline worker thread in
#: ``app.workers.upload_worker.run_worker_thread``. Matched by name because
#: that is the only handle the API process keeps on it.
_WORKER_THREAD_NAME = "upload-worker"

#: A queued job older than this has not been picked up by anything, whatever
#: shape the worker is deployed in. Generous relative to the worker's idle
#: poll interval so a momentarily busy worker is never reported as dead.
_QUEUE_LAG_ALERT_SECONDS = 300


def _worker_status() -> dict:
    """Report whether uploads are actually being processed.

    Two independent signals, because neither alone is sufficient:

    * **thread liveness** answers "is the worker running *in this process*",
      which is the deployment the Pi uses (``TCKDB_INLINE_WORKER=true``). It
      cannot see a worker running as a separate process.
    * **queue lag** answers "is anything picking work up", which holds for
      *any* deployment shape — but only tells you something when there is
      work to pick up.

    The reason the obvious signal is missing: ``upload_job.heartbeat_at`` is
    written only while a job is being processed, so an idle worker is
    indistinguishable from a dead one by heartbeat alone. That is why thread
    liveness is checked directly rather than inferred.
    """
    inline = os.getenv("TCKDB_INLINE_WORKER", "false").lower() == "true"
    thread_alive = any(
        thread.name == _WORKER_THREAD_NAME and thread.is_alive()
        for thread in threading.enumerate()
    )

    status: dict = {
        "inline": inline,
        "thread_alive": thread_alive if inline else None,
    }

    session = SessionLocal()
    try:
        row = session.execute(
            text(
                "SELECT count(*) AS queued,"
                "       min(created_at) AS oldest"
                "  FROM upload_job WHERE status = 'queued'"
            )
        ).one()
        queued = int(row.queued or 0)
        oldest_age = None
        if row.oldest is not None:
            oldest = row.oldest
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            oldest_age = (datetime.now(timezone.utc) - oldest).total_seconds()
        status["queued"] = queued
        status["oldest_queued_age_seconds"] = (
            None if oldest_age is None else round(oldest_age, 1)
        )
        status["queue_stalled"] = bool(
            oldest_age is not None and oldest_age > _QUEUE_LAG_ALERT_SECONDS
        )
    except SQLAlchemyError as exc:
        logger.warning("worker status: upload_job query failed: %r", exc)
        status["queued"] = None
        status["oldest_queued_age_seconds"] = None
        status["queue_stalled"] = None
    finally:
        session.close()

    # Healthy unless something positively says otherwise. An inline worker
    # whose thread has died is broken even with an empty queue -- that is the
    # silent failure this exists to surface.
    if inline and not thread_alive:
        status["healthy"] = False
        status["reason"] = "inline worker thread is not running"
    elif status.get("queue_stalled"):
        status["healthy"] = False
        status["reason"] = (
            f"oldest queued job is {status['oldest_queued_age_seconds']}s old; "
            "nothing is claiming work"
        )
    else:
        status["healthy"] = True
        status["reason"] = None
    return status


# ---------------------------------------------------------------------------
# Artifact storage
# ---------------------------------------------------------------------------

#: Per-connection and per-read bounds for the storage probe. Deliberately far
#: below any client's patience: the probe's job is to answer, not to succeed.
_STORAGE_PROBE_CONNECT_TIMEOUT = 1.5
_STORAGE_PROBE_READ_TIMEOUT = 1.5

#: Hard wall-clock ceiling for the probe, enforced outside botocore. botocore's
#: own timeouts do not cover every way a call can stall (DNS resolution is the
#: usual one), so the probe runs on a worker thread and the request thread
#: simply stops waiting. A dead endpoint must degrade ``/status``, never hang
#: it -- an alerting endpoint that times out is read as "host down", which is a
#: different page of the runbook from "storage is unreachable".
_STORAGE_PROBE_DEADLINE_SECONDS = 4.0

#: Two workers so a probe stuck on a black-holed endpoint cannot starve the
#: next poll; abandoned threads unwind on their own once botocore's timeouts
#: fire. Threads are created lazily, so an idle deployment pays nothing.
_STORAGE_PROBE_POOL = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="storage-probe"
)

#: S3 error codes meaning "the endpoint answered, but this bucket is not
#: usable". Kept apart from transport failures on purpose -- see below.
_BUCKET_MISSING_CODES = {"404", "NoSuchBucket", "NotFound"}
_BUCKET_FORBIDDEN_CODES = {
    "401",
    "403",
    "AccessDenied",
    "AllAccessDisabled",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
}


def _sanitized_endpoint(raw: str) -> str:
    """Return *raw* reduced to ``scheme://host:port``.

    Strips any ``user:password@`` userinfo and any path/query so a
    credential pasted into ``S3_ENDPOINT_URL`` cannot reach a public
    endpoint. What survives is the one fact an operator needs.
    """
    try:
        parts = urlsplit(raw)
        if not parts.scheme or not parts.hostname:
            return "(unset)" if not raw else "(unparseable)"
        netloc = parts.hostname
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        return f"{parts.scheme}://{netloc}"
    except ValueError:
        return "(unparseable)"


def _probe_bucket() -> dict:
    """``head_bucket`` once, and classify the outcome.

    Runs on the probe pool, so it must return rather than raise.
    """
    client = artifact_storage._get_s3_client(
        config=BotoConfig(
            connect_timeout=_STORAGE_PROBE_CONNECT_TIMEOUT,
            read_timeout=_STORAGE_PROBE_READ_TIMEOUT,
            # Exactly one attempt. Retries multiply the wall-clock cost of
            # the exact failure this probe exists to report quickly, and a
            # transient blip is not worth hiding from a 5-minute poll.
            #
            # ``legacy`` rather than ``standard`` because it is what
            # measurably does this: against a black-holed address,
            # ``{"mode": "standard", "max_attempts": 1}`` still spent ~3.2 s
            # (two connect attempts), while this spends one connect_timeout,
            # ~1.5 s.
            retries={"max_attempts": 0, "mode": "legacy"},
        )
    )
    bucket = artifact_storage.S3_BUCKET
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        # An HTTP response came back, so the endpoint is reachable. The
        # bucket is the problem, and it has a different fix.
        code = str(exc.response.get("Error", {}).get("Code", "")) or "unknown"
        if code in _BUCKET_MISSING_CODES:
            return {
                "healthy": False,
                "reachable": True,
                "reason": f"bucket {bucket!r} does not exist",
            }
        if code in _BUCKET_FORBIDDEN_CODES:
            return {
                "healthy": False,
                "reachable": True,
                "reason": (
                    f"bucket {bucket!r} exists or is hidden, but these "
                    f"credentials are refused (S3 code {code})"
                ),
            }
        return {
            "healthy": False,
            "reachable": True,
            "reason": f"bucket {bucket!r} check failed (S3 code {code})",
        }
    except BotoCoreError as exc:
        # No HTTP response at all: connection refused, DNS failure, TLS
        # failure, timeout. Nothing is wrong with the bucket; the address
        # is wrong or the service is down.
        return {
            "healthy": False,
            "reachable": False,
            "reason": f"cannot reach object store ({type(exc).__name__})",
        }
    except Exception as exc:  # pragma: no cover - defensive
        # /status must answer under every failure, including ones this
        # module has not anticipated.
        return {
            "healthy": False,
            "reachable": None,
            "reason": f"storage probe failed ({type(exc).__name__})",
        }
    return {"healthy": True, "reachable": True, "reason": None}


def _artifact_storage_status() -> dict:
    """Report whether artifact uploads can actually be stored.

    Artifact storage is a hard dependency: with it down, every
    artifact-bearing upload returns 503 while the database and the worker
    are perfectly fine. On 2026-08-05 that happened for real -- a
    containerised API inherited ``S3_ENDPOINT_URL=http://127.0.0.1:9000``
    from a host deployment, where inside the container it is the
    container's own loopback -- and ``/status`` reported healthy
    throughout, so nothing alerted and the deploy verification passed.

    Three deliberate choices, each of which was a way to get this wrong:

    * **``head_bucket``, not a write.** A round trip would make ``/status``
      itself a source of load and of garbage objects, and it can fail for
      reasons that have nothing to do with availability.
    * **``endpoint`` and ``bucket`` are reported.** They are what makes the
      failure legible in seconds rather than after reading source: the
      incident's whole content was that the endpoint was the wrong one,
      and every individual value in the config was still valid. The URL is
      reduced to ``scheme://host:port`` first, so no credential can ride
      out on a public endpoint.
    * **``reachable`` splits transport failure from bucket failure.** "The
      endpoint is unreachable" and "the endpoint answered and refused the
      bucket" send an operator to different places -- the network vs the
      credentials or the bucket name. The ``database`` component makes the
      same split for the same reason.
    """
    block: dict = {
        "endpoint": _sanitized_endpoint(artifact_storage.S3_ENDPOINT_URL),
        "bucket": artifact_storage.S3_BUCKET,
    }
    future = _STORAGE_PROBE_POOL.submit(_probe_bucket)
    try:
        outcome = future.result(timeout=_STORAGE_PROBE_DEADLINE_SECONDS)
    except FutureTimeoutError:
        future.cancel()
        logger.warning(
            "status: artifact storage probe exceeded %ss against %s",
            _STORAGE_PROBE_DEADLINE_SECONDS,
            block["endpoint"],
        )
        outcome = {
            "healthy": False,
            "reachable": False,
            "reason": (
                f"object store did not respond within "
                f"{_STORAGE_PROBE_DEADLINE_SECONDS}s"
            ),
        }
    if outcome["healthy"] is not True:
        logger.warning(
            "status: artifact storage unhealthy: endpoint=%s bucket=%s reason=%s",
            block["endpoint"],
            block["bucket"],
            outcome["reason"],
        )
    block.update(outcome)
    return block


@router.get("/health")
def health() -> dict:
    """Liveness probe — confirms the API can reach the database."""
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    finally:
        session.close()
    return {"status": "ok"}


@router.get("/readyz")
def readyz():
    """Readiness probe.

    Returns 200 with the current Alembic revision when the database is
    reachable and the schema has been migrated. Returns 503 with a
    stable error envelope when either check fails. The response shape
    is deliberately narrow — no DB URL, no driver text, no hostname.

    Artifact storage is deliberately not checked here: with the object
    store down, reads, queries, and uploads without attached files all
    still work, so removing the instance from rotation would replace a
    partial outage with a total one. Storage is reported on ``/status``.
    """
    session = SessionLocal()
    try:
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            logger.warning("readyz: database SELECT 1 failed: %r", exc)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "database": "error",
                    "code": "database_unavailable",
                },
            )

        try:
            revision = session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            logger.warning("readyz: alembic_version lookup failed: %r", exc)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "database": "ok",
                    "code": "schema_not_initialized",
                },
            )

        if revision is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "database": "ok",
                    "code": "schema_not_initialized",
                },
            )

        return {
            "status": "ready",
            "database": "ok",
            "alembic_revision": revision,
        }
    finally:
        session.close()


@router.get("/status")
def status():
    """Operational summary for humans and for the alerting checker.

    ``/health`` and ``/readyz`` deliberately return a tiny fixed shape because
    load balancers consume them. This endpoint is the richer one: it answers
    "is anything wrong, and what" in a single request, so an operator has one
    URL to open and a checker has one URL to poll.

    It returns 200 with ``status: "degraded"`` rather than a 5xx when a
    component is unhealthy. A non-200 here would make the endpoint itself
    indistinguishable from the outage it is trying to describe, and a checker
    could not tell "the site is down" from "the site is up and telling me the
    worker died" -- which are different pages of the runbook.

    Components: ``database``, ``worker``, ``artifact_storage``. Each is a hard
    dependency that can fail alone while the others stay green.

    Discloses no credentials and no driver text. It does disclose the Alembic
    revision and the object-store ``scheme://host:port`` and bucket name -- a
    deliberate, bounded trade: those are what let an operator diagnose a
    misconfigured deployment from the outside, and none of them grants access.
    """
    components: dict = {}

    session = SessionLocal()
    try:
        # Two separate failures with two separate fixes, so they get two
        # separate reasons. Collapsing them into one ``except`` reports an
        # un-migrated database as "unreachable", which sends an operator to
        # check the network when the answer is `alembic upgrade head`. Caught
        # by running this against a fresh database, where /health returned 200
        # while /status claimed the database was unreachable.
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            logger.warning("status: database SELECT 1 failed: %r", exc)
            components["database"] = {
                "healthy": False,
                "alembic_revision": None,
                "server_encoding": None,
                "reason": "database unreachable",
            }
        else:
            try:
                revision = session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
            except SQLAlchemyError as exc:
                logger.warning("status: alembic_version lookup failed: %r", exc)
                revision = None
            # Reported, not judged. A non-UTF8 cluster is a real hazard --
            # SQL_ASCII validates nothing and aborts on the first byte it
            # cannot handle -- but it is a permanent property of the cluster
            # that only a dump and restore can change, so degrading /status
            # on it would nag every five minutes about something no restart
            # can fix. Startup logs it as an error once, which is the right
            # loudness; this makes it answerable from outside, which is what
            # turns "uploads sometimes fail" into a five-second diagnosis.
            components["database"] = {
                "healthy": revision is not None,
                "alembic_revision": revision,
                "server_encoding": server_encoding(session),
                "reason": (
                    None
                    if revision is not None
                    else "schema not initialized (run alembic upgrade head)"
                ),
            }
    finally:
        session.close()

    components["worker"] = _worker_status()
    components["artifact_storage"] = _artifact_storage_status()

    unhealthy = sorted(
        name for name, block in components.items() if block.get("healthy") is False
    )
    return {
        "status": "ok" if not unhealthy else "degraded",
        "degraded": unhealthy,
        "components": components,
    }
