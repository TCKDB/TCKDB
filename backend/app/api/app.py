"""FastAPI application factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config import Settings, settings
from app.api.errors import register_exception_handlers
from app.api.logging_config import configure_logging
from app.api.public_openapi import install_hosted_openapi
from app.api.rate_limit import RateLimitMiddleware
from app.api.request_id import RequestIDMiddleware
from app.api.router import api_router
from app.api.startup_checks import (
    report_artifact_storage_at_startup,
    report_database_encoding_at_startup,
    validate_deployment_safety,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the inline upload worker, and report on hard dependencies.

    Set ``TCKDB_INLINE_WORKER=true`` to run a worker inside the API process.
    The default is ``false`` — run the worker as a separate process instead
    (``python -m app.workers.upload_worker``), which is recommended for
    production.

    Two boot-time probes write one line each into the container log. Both
    describe faults that are fully detectable from the process's first
    second and were previously invisible until something downstream broke:
    an object store that cannot be reached (503 on the first
    artifact-bearing upload) and a database cluster that is not UTF-8 (an
    aborted transaction on the first non-ASCII character). Neither blocks
    or fails startup — see :mod:`app.api.startup_checks`.

    "Neither blocks" is a bounded claim and both probes now enforce it.
    Each runs against its dependency behind a hard wall-clock deadline of
    a few seconds, so the worst case a dead dependency can impose on boot
    is that pause and a log line. It was true of the storage probe and
    merely asserted of the database one, which against a black-holed host
    held the lifespan for the OS TCP timeout — around two minutes of a
    process that was up, listening to nothing and answering nothing.
    """
    if os.getenv("TCKDB_INLINE_WORKER", "false").lower() == "true":
        from app.workers.upload_worker import run_worker_thread
        run_worker_thread()

    # Opt-out for test fixtures and offline dev, which build the app hundreds
    # of times and have neither an object store to reach nor a reason to
    # re-check one cluster's encoding per test. Defaults to on, because the
    # deployment that needs this most is the one nobody remembered to
    # configure.
    if os.getenv("TCKDB_STARTUP_PROBES", "true").lower() == "true":
        report_artifact_storage_at_startup()
        report_database_encoding_at_startup()

    yield

    # Daemon thread dies with the process — nothing to clean up.


#: Paths FastAPI registers for each of the three built-in doc surfaces.
#: ``REDOC_PATH`` and ``OPENAPI_PATH`` are both consumed below, by
#: ``_docs_kwargs``. ``SWAGGER_PATH`` is documentation-only: nothing in
#: code imports it (FastAPI's own ``/docs`` default is what actually
#: applies), and it is spelled out here only so a reader of this module
#: sees all three doc paths named in one place. ``REDOC_PATH`` used to
#: live in ``app/api/landing.py`` (the dead root-``/`` HTML page,
#: deleted) because the landing page linked to ReDoc; it moved here, and
#: only here, with that page's deletion.
SWAGGER_PATH = "/docs"
REDOC_PATH = "/redoc"
OPENAPI_PATH = "/openapi.json"


def _docs_kwargs(settings_obj: Settings) -> dict[str, str | None]:
    """Resolve which of ``/docs``, ``/redoc`` and ``/openapi.json`` exist.

    Passing ``None`` for one of these URLs prevents FastAPI from
    registering the corresponding route at all, so a disabled surface
    returns the same 404 as any other unrouted path -- there is nothing
    behind it to be reached by guessing.

    Three configurations, in precedence order:

    * ``EXPOSE_API_DOCS=true`` (the local/dev default) -- all three,
      Swagger UI included. Unchanged from before this function existed.
    * ``EXPOSE_API_DOCS=false`` **and**
      ``EXPOSE_API_REFERENCE=true`` -- ReDoc and the OpenAPI document,
      no Swagger UI. This is the hosted posture: publish the contract
      as a static reference, do not hand an anonymous reader a request
      console pointed at the live deployment.
    * both false (the hosted default, and the default for any
      deployment that sets neither) -- none of the three.

    ``EXPOSE_API_DOCS`` therefore keeps its exact previous meaning for
    Swagger, and no deployment becomes more exposed by upgrading: the
    middle case has to be opted into.
    """
    if settings_obj.expose_api_docs:
        return {}
    if settings_obj.expose_api_reference:
        return {"docs_url": None, "redoc_url": REDOC_PATH, "openapi_url": OPENAPI_PATH}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


def create_app() -> FastAPI:
    configure_logging()
    # Refuse to boot a hosted/public deployment with unsafe settings.
    # No-op in DEPLOYMENT_MODE=local (the test/dev default), so existing
    # fixtures are unaffected. See app/api/startup_checks.py and
    # docs/deployment/production_checklist.md.
    validate_deployment_safety(settings)
    docs_kwargs = _docs_kwargs(settings)
    app = FastAPI(
        title="TCKDB",
        version="0.1.0",
        description="Thermochemical and Kinetics Database API",
        lifespan=_lifespan,
        **docs_kwargs,
    )
    # Middleware ordering. Starlette runs middleware in
    # most-recently-added-first order, so the final inbound chain
    # below is ``RequestID -> RateLimit -> CORS -> router``. The
    # request id has to be set first so every downstream layer
    # (rate-limit log lines, error envelopes, route handlers) can
    # read it from ``request.state.request_id`` or the logging
    # context.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=settings.cors_allow_methods,
            allow_headers=settings.cors_allow_headers,
        )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.include_router(api_router, prefix="/api/v1")
    register_exception_handlers(app)
    install_hosted_openapi(app)
    return app
