"""FastAPI application factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
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
    app = FastAPI(
        title="TCKDB",
        version="0.1.0",
        description="Thermochemical and Kinetics Database API",
        lifespan=_lifespan,
    )
    app.include_router(api_router, prefix="/api/v1")
    register_exception_handlers(app)
    return app
