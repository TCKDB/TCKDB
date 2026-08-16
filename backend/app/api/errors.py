"""API exception types and FastAPI exception handlers."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, NoResultFound, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.error_contract import (
    CodedValidationError,
    error_envelope,
    validation_detail_code,
    validation_detail_context,
)
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
)
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
    response envelope. Without a code the envelope falls back to the
    generic ``resource_not_found``, which tells a client nothing the status
    line did not.

    ``context`` carries the structured half of the refusal — which field
    named the missing row, and what it named. A 404 raised from inside an
    upload payload needs it: "statmech not found" is useless when the
    payload cited four refs, and the alternative is a client string-matching
    prose. It is deep-sanitised by :func:`app.api.error_contract.error_envelope`
    like any other context, and must never carry a database primary key
    (DR-0028 Requirement 2).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context = dict(context or {})


def not_found(
    kind: str,
    *,
    row_id: object = None,
    ref: str | None = None,
    code: str | None = None,
    context: dict[str, object] | None = None,
) -> NotFoundError:
    """Build a 404 that names the resource without disclosing its row id.

    The obvious way to write a 404 is ``f"{kind} not found ({kind}_id=
    {row_id})"``, which is why roughly thirty of them were written that
    way. It reads helpfully and it is wrong twice over. A row id is an
    implementation detail of one database instance -- it does not survive
    a restore, it does not agree across the hosted deployment and a lab
    self-host, and no public surface is keyed on it -- so a client that
    learns to read it builds against something TCKDB never promised to
    keep stable. And the id is useless to the caller anyway: the whole
    point of a 404 is that the row is not there.

    Whoever actually needs the id is the operator reading the log, so it
    goes there. A public ref *is* echoed, because the caller supplied it
    and quoting it back is what makes a 404 diagnosable at all.

    :param kind: The resource, in the vocabulary the API uses for it
        (``"calculation"``, ``"species_entry"``).
    :param row_id: The internal id that was looked up. Logged, never
        returned.
    :param ref: A public ref the caller supplied, echoed into the body.
    :param code: Stable application code for the envelope.
    :param context: Structured detail for the envelope — never a row id.
    """
    if row_id is not None:
        logger.info("404 %s not found: row_id=%s ref=%s", kind, row_id, ref)
    detail = f"{kind} not found"
    if ref is not None:
        detail += f" ({kind}_ref={ref!r})"
    return NotFoundError(detail, code=code, context=context)


class DataIntegrityError(Exception):
    """Persisted data violates a scientific invariant assumed by the API.

    Returned as HTTP 500 — the request is well-formed, but the database
    contains a row combination the serializer cannot safely represent
    (e.g. a polymorphic kinetics record with zero or multiple subtype
    payloads).
    """


def _value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    # ``CodedValueError`` is the backend subclass; a wire-schema check raises
    # the base directly, because ``tckdb_schemas`` may not import ``app``.
    if isinstance(exc, CodedValidationError):
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
            context=validation_detail_context(validation_detail),
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
                context=validation_detail_context(details),
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
            str(exc),
            code=code,
            context=getattr(exc, "context", None),
            fallback_code="resource_not_found",
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
#
# SQLSTATE is the *floor*, not the answer. It says a check constraint
# fired; it cannot say which scientific rule that constraint encodes, so
# on its own it renders "an atom changed element across this reaction"
# and "a required column was null" as the same ``state_conflict``. A
# constraint that the scientific check register names carries its own
# code and sentence, looked up by name in ``_constraint_rejections``
# below; everything else still falls back to the SQLSTATE bucket.

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


@lru_cache(maxsize=1)
def _constraint_rejections() -> dict[str, tuple[str, str]]:
    """Scientific 409 codes, keyed on the constraint PostgreSQL names.

    Imported lazily and cached because the register pulls in the service
    and chemistry modules that declare its entries, several of which
    import this module for :class:`DomainError` -- so a module-level
    import would be a cycle. Nothing here runs until the first integrity
    error, by which point the application is fully imported.

    The mapping is *derived*, never written here. A hand-kept table in
    this module would be a second place to remember a constraint rename;
    building it from the register means the rename fails
    ``test_database_object_exists_in_live_schema`` -- which queries
    ``pg_constraint`` -- before it can silently downgrade a scientific
    refusal back to a generic ``state_conflict``.
    """
    from app.scientific_checks.declarations import constraint_rejections

    return constraint_rejections()


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
    context: dict[str, Any] = {}
    # Named before classified. A constraint that encodes chemistry is a
    # stronger guarantee than any Python check -- no write path can get
    # around it -- and until now it was the one a client was told least
    # about, because every violation collapsed into its SQLSTATE bucket.
    # Keyed on the constraint name rather than on the driver's message
    # text: parsing prose is what the typed envelope exists to replace.
    rejection = _constraint_rejections().get(constraint) if constraint else None
    if rejection is not None:
        code, message = rejection
        # The schema object name, not a row id. It is the key into
        # ``docs/guides/scientific_check_register.md``, so a client (or a
        # human reading a traceback) can look up which scientific position
        # refused the write and what the escape hatch is.
        context["constraint"] = constraint
    elif constraint == IDEMPOTENCY_UNIQUE_CONSTRAINT:
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
            "context": context,
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


def _artifact_integrity_handler(
    request: Request, exc: ArtifactIntegrityError
) -> JSONResponse:
    """502, and never 503 — retrying cannot fix a digest that will not match.

    Reached from the upload path, where ``store_artifact`` verifies an
    existing content-addressed object before attaching another row to the
    shared key. Previously this was relabelled ``ArtifactStorageUnavailable``
    on its way out, which told the uploader to retry a permanent condition
    and named the wrong subsystem in the journal. The durable record is
    written at the detection site (``app.services.artifact_integrity``), not
    here: a handler runs only for the requests that happen to surface, and
    detection happens in places that never see one.
    """
    logger.error(
        "ArtifactIntegrityError on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=502,
        content={
            "detail": (
                "A stored artifact failed integrity verification. This has "
                "been recorded; retrying will not clear it."
            ),
            "code": "artifact_integrity_failed",
            "context": {},
        },
    )


def _artifact_storage_unavailable_handler(
    request: Request, exc: ArtifactStorageUnavailable
) -> JSONResponse:
    """503 for the client; the actual reason for the operator.

    The public body stays deliberately vague -- it names a subsystem and
    says retry. That is right for a caller and useless for whoever has to
    fix it: before this log line, a storage outage appeared in the journal
    as a bare access-log ``503`` with no traceback and no cause, and
    finding out *why* meant reading source to locate the raise site. The
    raised message already carries the underlying botocore error (see
    :func:`app.services.artifact_persistence._store_and_record`), which
    names the endpoint it failed to reach; the only thing missing was
    somewhere to put it. Response shape is unchanged.
    """
    logger.warning(
        "ArtifactStorageUnavailable on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=exc,
    )
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
    app.add_exception_handler(ArtifactIntegrityError, _artifact_integrity_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ArtifactStorageUnavailable, _artifact_storage_unavailable_handler)  # type: ignore[arg-type]
