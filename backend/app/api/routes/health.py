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

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionLocal

router = APIRouter()
logger = logging.getLogger(__name__)


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
