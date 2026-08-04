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
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionLocal

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

    Discloses no hostnames, credentials, or driver text: it is public, on the
    same host as the docs.
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
            components["database"] = {
                "healthy": revision is not None,
                "alembic_revision": revision,
                "reason": (
                    None
                    if revision is not None
                    else "schema not initialized (run alembic upgrade head)"
                ),
            }
    finally:
        session.close()

    components["worker"] = _worker_status()

    unhealthy = sorted(
        name for name, block in components.items() if block.get("healthy") is False
    )
    return {
        "status": "ok" if not unhealthy else "degraded",
        "degraded": unhealthy,
        "components": components,
    }
