"""FastAPI application factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config import settings
from app.api.errors import register_exception_handlers
from app.api.rate_limit import RateLimitMiddleware
from app.api.router import api_router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the inline upload worker thread when opted in via env var.

    Set ``TCKDB_INLINE_WORKER=true`` to run a worker inside the API process.
    The default is ``false`` — run the worker as a separate process instead
    (``python -m app.workers.upload_worker``), which is recommended for
    production.
    """
    thread = None
    if os.getenv("TCKDB_INLINE_WORKER", "false").lower() == "true":
        from app.workers.upload_worker import run_worker_thread
        thread = run_worker_thread()

    yield

    # Daemon thread dies with the process — nothing to clean up.


def create_app() -> FastAPI:
    # Passing ``None`` for the docs URL prevents FastAPI from
    # registering the route. Hosted deployments default to off via
    # ``EXPOSE_API_DOCS=false`` (see settings); local/dev leaves it on.
    docs_kwargs: dict[str, str | None] = {}
    if not settings.expose_api_docs:
        docs_kwargs.update(docs_url=None, redoc_url=None, openapi_url=None)
    app = FastAPI(
        title="TCKDB",
        version="0.1.0",
        description="Thermochemical and Kinetics Database API",
        lifespan=_lifespan,
        **docs_kwargs,
    )
    # CORS first so preflight responses are short-circuited before
    # any other middleware runs. An empty allow-list means we do
    # *not* register the middleware at all — the default
    # browser-rejects-cross-origin posture is the right hosted
    # default.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=settings.cors_allow_methods,
            allow_headers=settings.cors_allow_headers,
        )
    app.add_middleware(RateLimitMiddleware)
    app.include_router(api_router, prefix="/api/v1")
    register_exception_handlers(app)
    return app
