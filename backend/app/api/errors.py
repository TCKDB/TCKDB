"""API exception types and FastAPI exception handlers."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, NoResultFound

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Business logic violation (valid payload, invalid operation)."""


class NotFoundError(Exception):
    """Explicit 404 raised by service code."""


class DataIntegrityError(Exception):
    """Persisted data violates a scientific invariant assumed by the API.

    Returned as HTTP 500 — the request is well-formed, but the database
    contains a row combination the serializer cannot safely represent
    (e.g. a polymorphic kinetics record with zero or multiple subtype
    payloads).
    """


def _value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def _data_integrity_error_handler(
    _request: Request, exc: DataIntegrityError
) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def _domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


def _no_result_found_handler(
    _request: Request, _exc: NoResultFound
) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"detail": "Resource not found"}
    )


# ---------------------------------------------------------------------------
# Integrity-error sanitization
# ---------------------------------------------------------------------------
#
# Public responses must not leak raw psycopg/PostgreSQL text. We classify
# the failure by SQLSTATE (stable across PG versions) and return a small,
# stable envelope. Full driver detail goes to the server log.

_SQLSTATE_TO_CATEGORY: dict[str, tuple[str, str]] = {
    # (category code, sanitized public message)
    "23505": ("unique_conflict", "Resource conflicts with an existing record."),
    "23503": (
        "reference_conflict",
        "Request references an entity that does not exist or is still in use.",
    ),
    "23514": ("state_conflict", "Request violates a consistency rule."),
    "23502": ("state_conflict", "Request is missing a required field."),
    "23P01": ("state_conflict", "Request violates a consistency rule."),
}

_FALLBACK = ("integrity_conflict", "Integrity constraint violation.")


def _classify_integrity_error(exc: IntegrityError) -> tuple[str, str]:
    """Return (category_code, public_message) for an IntegrityError.

    Falls back to a generic integrity_conflict when the SQLSTATE cannot
    be read or is not in the known set, so unexpected driver shapes
    never leak raw text.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if isinstance(sqlstate, str):
        mapped = _SQLSTATE_TO_CATEGORY.get(sqlstate)
        if mapped is not None:
            return mapped
    return _FALLBACK


def _integrity_error_handler(
    request: Request, exc: IntegrityError
) -> JSONResponse:
    code, message = _classify_integrity_error(exc)

    diag: dict[str, Any] = {}
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    if sqlstate is not None:
        diag["sqlstate"] = sqlstate
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint:
        diag["constraint"] = constraint
    logger.warning(
        "IntegrityError on %s %s: code=%s diag=%s orig=%r",
        request.method,
        request.url.path,
        code,
        diag,
        orig,
        exc_info=exc,
    )

    return JSONResponse(
        status_code=409,
        content={"detail": message, "code": code},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all custom exception handlers to *app*."""
    app.add_exception_handler(ValueError, _value_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(DomainError, _domain_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(NotFoundError, _not_found_handler)  # type: ignore[arg-type]
    app.add_exception_handler(DataIntegrityError, _data_integrity_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(NoResultFound, _no_result_found_handler)  # type: ignore[arg-type]
    app.add_exception_handler(IntegrityError, _integrity_error_handler)  # type: ignore[arg-type]
