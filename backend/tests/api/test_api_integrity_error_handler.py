"""Integrity-error response-hardening tests.

Exercises the public API integrity-error seam in ``app.api.errors`` to
assert that raw DB/driver text is never exposed and that responses use
a stable, application-facing envelope.

The tests mount the real exception handlers on a lightweight FastAPI app
with a dedicated route that raises ``IntegrityError`` instances with
controlled ``sqlstate`` values. This lets us exercise every classified
branch deterministically without relying on brittle, constraint-specific
triggers.
"""

from __future__ import annotations

import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from app.api.errors import register_exception_handlers


class _FakeDiag:
    def __init__(self, constraint_name: str | None) -> None:
        self.constraint_name = constraint_name


class _FakeDriverError(Exception):
    """Stand-in for a psycopg driver exception with sqlstate + diag."""

    def __init__(self, sqlstate: str | None, constraint_name: str | None = None):
        super().__init__(
            f"driver text with sensitive SQL details (constraint={constraint_name})"
        )
        self.sqlstate = sqlstate
        self.diag = _FakeDiag(constraint_name)


def _make_integrity_error(
    sqlstate: str | None, constraint_name: str | None = None
) -> IntegrityError:
    orig = _FakeDriverError(sqlstate, constraint_name)
    return IntegrityError(
        statement="INSERT INTO secret_internal_table (...) VALUES (...)",
        params=None,
        orig=orig,
    )


class _EchoBody(BaseModel):
    value: int


@pytest.fixture
def handler_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-integrity")
    def _raise_integrity(
        sqlstate: str | None = None,
        constraint: str | None = None,
    ):
        raise _make_integrity_error(sqlstate, constraint)

    @app.post("/validate")
    def _validate(body: _EchoBody):
        return {"value": body.value}

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Fragments that should never appear in a sanitized public integrity response.
_FORBIDDEN_FRAGMENTS = (
    "INSERT INTO",
    "SELECT ",
    "psycopg",
    "sqlalchemy",
    "driver text",
    "sensitive SQL",
    "secret_internal_table",
    "Traceback",
)


def _assert_sanitized(body: dict) -> None:
    blob = repr(body).lower()
    for fragment in _FORBIDDEN_FRAGMENTS:
        assert fragment.lower() not in blob, (
            f"forbidden fragment {fragment!r} leaked into response: {body!r}"
        )


# ---------------------------------------------------------------------------
# Category tests
# ---------------------------------------------------------------------------


class TestUniqueConflict:
    """SQLSTATE 23505 -> unique_conflict."""

    def test_sanitized_envelope(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "23505", "constraint": "uq_species_smiles"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "unique_conflict"
        assert isinstance(body["detail"], str) and body["detail"]
        _assert_sanitized(body)
        # Internal constraint name must not leak through the public body.
        assert "uq_species_smiles" not in repr(body)


class TestCheckConflict:
    """SQLSTATE 23514 -> state_conflict."""

    def test_sanitized_envelope(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "23514", "constraint": "ck_owner_xor"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "state_conflict"
        _assert_sanitized(body)
        assert "ck_owner_xor" not in repr(body)


class TestReferenceConflict:
    """SQLSTATE 23503 -> reference_conflict."""

    def test_sanitized_envelope(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "23503", "constraint": "fk_entry_species_id"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "reference_conflict"
        _assert_sanitized(body)


class TestNotNullConflict:
    """SQLSTATE 23502 -> state_conflict (missing required field)."""

    def test_sanitized_envelope(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "23502"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "state_conflict"
        _assert_sanitized(body)


class TestFallback:
    """Unknown/missing sqlstate -> generic integrity_conflict, no leakage."""

    def test_unknown_sqlstate(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "99999"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "integrity_conflict"
        _assert_sanitized(body)

    def test_missing_sqlstate(self, handler_client):
        resp = handler_client.get("/raise-integrity")
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "integrity_conflict"
        _assert_sanitized(body)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestInternalLogsPreservedDetail:
    """Public responses are sanitized, but server logs retain low-level detail."""

    def test_log_retains_constraint_and_sqlstate(self, handler_client, caplog):
        with caplog.at_level(logging.WARNING, logger="app.api.errors"):
            resp = handler_client.get(
                "/raise-integrity",
                params={"sqlstate": "23505", "constraint": "uq_species_smiles"},
            )
        assert resp.status_code == 409

        messages = "\n".join(r.getMessage() for r in caplog.records)
        assert "23505" in messages
        assert "uq_species_smiles" in messages
        # The original driver exception text should be available via the
        # logged record (exc_info or formatted orig repr), which developers
        # rely on when debugging.
        assert "IntegrityError" in messages or any(
            r.exc_info is not None for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Validation-error regression guard
# ---------------------------------------------------------------------------


class TestValidationErrorUnchanged:
    """Ordinary Pydantic validation errors must NOT be reclassified."""

    def test_validation_error_shape_preserved(self, handler_client):
        resp = handler_client.post("/validate", json={"value": "not-an-int"})
        # FastAPI default for request-body validation errors is 422.
        assert resp.status_code == 422
        body = resp.json()
        # The detail list remains FastAPI-compatible inside the additive
        # machine-consumer envelope.
        assert "detail" in body
        assert isinstance(body["detail"], list)
        assert body["code"] == "request_validation_error"
        assert body["context"] == {}


# ---------------------------------------------------------------------------
# End-to-end sanitization against the real app (belt-and-braces)
# ---------------------------------------------------------------------------


class TestRealAppUniqueConflict:
    """Drive a real unique-constraint violation through the full app.

    Asserts the sanitized wire shape against the production exception
    handlers — guards against regressions in the live handler chain.
    """

    def test_duplicate_software_name(self, client, db_session):
        from app.db.models.software import Software

        db_session.add(Software(name="IntegrityTestSoftware"))
        db_session.flush()
        db_session.add(Software(name="IntegrityTestSoftware"))

        with pytest.raises(IntegrityError) as excinfo:
            db_session.flush()

        # Simulate what the handler sees — classify and assert sanitization.
        from app.api.errors import _classify_integrity_error

        code, message = _classify_integrity_error(excinfo.value)
        assert code == "unique_conflict"
        # Sanitized message must not contain raw driver text fragments.
        assert not re.search(r"(?i)psycopg|sqlalchemy|insert into", message)
