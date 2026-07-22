"""API exception types and FastAPI exception handlers."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, NoResultFound, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_contract import (
    CodedValueError,
    error_envelope,
    validation_detail_code,
)
from app.services.artifact_storage import ArtifactStorageUnavailable
from app.services.idempotency import (
    IDEMPOTENCY_UNIQUE_CONSTRAINT,
    IdempotencyConflict,
    InvalidIdempotencyKey,
)

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Business logic violation (valid payload, invalid operation)."""


class NotFoundError(Exception):
    """Explicit 404 raised by service code.

    Pass ``code`` to attach a stable application-facing code to the
    response envelope. Without a code the handler emits only
    ``{"detail": ...}`` (legacy behavior).
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class DataIntegrityError(Exception):
    """Persisted data violates a scientific invariant assumed by the API.

    Returned as HTTP 500 — the request is well-formed, but the database
    contains a row combination the serializer cannot safely represent
    (e.g. a polymorphic kinetics record with zero or multiple subtype
    payloads).
    """


def _value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    if isinstance(exc, CodedValueError):
        content = error_envelope(
            str(exc),
            code=exc.code,
            context=exc.context,
            fallback_code="validation_error",
        )
    else:
        validation_detail = (
            exc.errors() if callable(getattr(exc, "errors", None)) else str(exc)
        )
        content = error_envelope(
            str(exc),
            code=validation_detail_code(
                validation_detail, fallback="validation_error"
            ),
            fallback_code="validation_error",
        )
    return JSONResponse(status_code=422, content=content)


def _request_validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = exc.errors()
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            error_envelope(
                details,
                code=validation_detail_code(
                    details, fallback="request_validation_error"
                ),
                fallback_code="request_validation_error",
            )
        ),
    )


def _http_exception_handler(
    _request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(
            exc.detail,
            fallback_code=f"http_{exc.status_code}",
        ),
        headers=exc.headers,
    )


def _data_integrity_error_handler(
    _request: Request, exc: DataIntegrityError
) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_envelope(
            str(exc), fallback_code="data_integrity_error"
        ),
    )


def _domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_envelope(str(exc), fallback_code="domain_error"),
    )


def _not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
    code = getattr(exc, "code", None)
    return JSONResponse(
        status_code=404,
        content=error_envelope(
            str(exc), code=code, fallback_code="resource_not_found"
        ),
    )


def _no_result_found_handler(
    _request: Request, _exc: NoResultFound
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=error_envelope(
            "Resource not found", fallback_code="resource_not_found"
        ),
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
    if constraint == IDEMPOTENCY_UNIQUE_CONSTRAINT:
        code = "idempotency_conflict"
        message = (
            "Idempotency key was used concurrently for a different request."
        )
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
        content={
            "detail": message,
            "code": code,
            "category": "integrity_error",
            "context": {},
        },
    )


def _invalid_idempotency_key_handler(
    _request: Request, exc: InvalidIdempotencyKey
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_envelope(
            str(exc),
            code="invalid_idempotency_key",
            fallback_code="invalid_idempotency_key",
        ),
    )


def _artifact_storage_unavailable_handler(
    _request: Request, exc: ArtifactStorageUnavailable
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Artifact storage is temporarily unavailable. Retry later.",
            "code": "artifact_storage_unavailable",
            "context": {},
        },
    )


def _operational_error_handler(
    request: Request, exc: OperationalError
) -> JSONResponse:
    """Sanitize PostgreSQL ``OperationalError`` (statement timeout, etc.).

    The driver wraps :pep:`249` operational failures — statement
    timeouts (SQLSTATE 57014), admin shutdowns (57P01), connection
    drops, etc. — in :class:`OperationalError`. We classify by
    SQLSTATE so a query-timeout cancellation surfaces a stable
    ``query_timeout`` code without leaking the offending SQL.
    Anything else falls through to a generic ``database_unavailable``
    body; raw driver text stays in the server log.
    """
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    code = "database_unavailable"
    message = "Database temporarily unavailable. Retry shortly."
    status = 503
    if sqlstate == "57014":
        code = "query_timeout"
        message = (
            "The request exceeded the database query timeout. Narrow "
            "the query or contact a curator for bulk access."
        )
    logger.warning(
        "OperationalError on %s %s: code=%s sqlstate=%s orig=%r",
        request.method,
        request.url.path,
        code,
        sqlstate,
        orig,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status,
        content=error_envelope(
            message, code=code, fallback_code="database_error"
        ),
    )


def _idempotency_conflict_handler(
    _request: Request, exc: IdempotencyConflict
) -> JSONResponse:
    body = {
        "detail": "Idempotency key was already used with a different request payload.",
        "code": (
            "idempotency_in_progress" if exc.in_progress else "idempotency_conflict"
        ),
        "endpoint": exc.endpoint,
        "created_at": exc.created_at.isoformat(),
        "context": {
            "endpoint": exc.endpoint,
            "created_at": exc.created_at.isoformat(),
        },
    }
    return JSONResponse(status_code=409, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all custom exception handlers to *app*."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _request_validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValueError, _value_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(DomainError, _domain_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(NotFoundError, _not_found_handler)  # type: ignore[arg-type]
    app.add_exception_handler(DataIntegrityError, _data_integrity_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(NoResultFound, _no_result_found_handler)  # type: ignore[arg-type]
    app.add_exception_handler(IntegrityError, _integrity_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(OperationalError, _operational_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(InvalidIdempotencyKey, _invalid_idempotency_key_handler)  # type: ignore[arg-type]
    app.add_exception_handler(IdempotencyConflict, _idempotency_conflict_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ArtifactStorageUnavailable, _artifact_storage_unavailable_handler)  # type: ignore[arg-type]
