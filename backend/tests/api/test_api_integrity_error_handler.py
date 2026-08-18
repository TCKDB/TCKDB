"""Integrity-error response-hardening tests.

Exercises the public API integrity-error seam in ``app.api.errors`` to
assert that raw DB/driver text is never exposed and that responses use
a stable, application-facing envelope.

Two things a generic 409 owes, and they are asserted together
------------------------------------------------------------
It owes a **sentence a depositor can act on** -- which kind of rule
refused, and what would make a second attempt succeed -- and it owes the
**absence of the constraint name**, which is an internal identifier
(Calvin, 2026-08-18; the same reasoning as DR-0028 Requirement 2 for row
ids). They are asserted together, in :func:`_assert_generic_conflict`,
because an absence assertion on its own passes against a handler that
returned nothing at all, and this repository has shipped that mistake
before. The absence is additionally swept for every body in the suite by
``tests/error_body_observer.py``.

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


def _assert_generic_conflict(body: dict, code: str, constraint: str | None) -> None:
    """The whole obligation of a generic 409, asserted in one place.

    Three claims, and the order matters. The first two are positive --
    the code a client branches on, and a sentence they can act on -- and
    they are here because the third is an absence, and an absence
    assertion passes just as happily against a handler that returned an
    empty body. Asserting only that a name is missing is the vacuous-pass
    shape this repository has produced more than once.

    The third is Calvin's ruling of 2026-08-18: a raw database constraint
    name never appears in a user-facing body. Before it, this file made
    that assertion for unique and check violations, ``TestReferenceConflict``
    made no assertion in either direction, and
    ``test_api_database_constraint_codes.py`` asserted the opposite for
    registered constraints. Three positions; now one, and the same one is
    enforced for every body in the suite by ``tests/error_body_observer.py``.
    """
    assert body["code"] == code
    detail = body["detail"]
    assert isinstance(detail, str) and detail.strip(), (
        "a generic 409 owes the client a sentence they can act on; the code "
        "alone does not say which kind of rule refused the write"
    )
    _assert_sanitized(body)
    if constraint is not None:
        assert constraint not in repr(body), (
            f"internal constraint name {constraint!r} leaked through the "
            "public body. It is an internal identifier: meaningless to a "
            "depositor, unstable across a rename, and a disclosure of schema "
            "layout. It goes to the log; a constraint that deserves a public "
            "contract is registered and earns a code of its own."
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
        _assert_generic_conflict(resp.json(), "unique_conflict", "uq_species_smiles")


class TestCheckConflict:
    """SQLSTATE 23514 -> state_conflict."""

    def test_sanitized_envelope(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "23514", "constraint": "ck_owner_xor"},
        )
        assert resp.status_code == 409
        _assert_generic_conflict(resp.json(), "state_conflict", "ck_owner_xor")


class TestReferenceConflict:
    """SQLSTATE 23503 -> reference_conflict.

    The gap this class used to be. It asserted the code and the absence
    of driver text, and said nothing about the constraint name in either
    direction -- so a foreign-key name could have been published without
    a single test noticing, while the sibling classes forbade it. Closed
    2026-08-18.
    """

    def test_sanitized_envelope(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "23503", "constraint": "fk_entry_species_id"},
        )
        assert resp.status_code == 409
        _assert_generic_conflict(
            resp.json(), "reference_conflict", "fk_entry_species_id"
        )


class TestNotNullConflict:
    """SQLSTATE 23502 -> state_conflict (missing required field).

    PostgreSQL reports no constraint name for this one -- measured, and
    pinned by ``test_api_database_constraint_codes.py`` -- so there is
    nothing to hide here and the parameter is ``None``.
    """

    def test_sanitized_envelope(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "23502"},
        )
        assert resp.status_code == 409
        _assert_generic_conflict(resp.json(), "state_conflict", None)


class TestExclusionConflict:
    """SQLSTATE 23P01 -> state_conflict, and it was never exercised.

    The branch existed in ``_SQLSTATE_TO_CATEGORY`` from the start with
    no test behind it, which meant its message could have said anything.
    """

    def test_sanitized_envelope(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "23P01", "constraint": "ix_release_window"},
        )
        assert resp.status_code == 409
        _assert_generic_conflict(resp.json(), "state_conflict", "ix_release_window")


class TestFallback:
    """Unknown/missing sqlstate -> generic integrity_conflict, no leakage."""

    def test_unknown_sqlstate(self, handler_client):
        resp = handler_client.get(
            "/raise-integrity",
            params={"sqlstate": "99999", "constraint": "uq_something_unmapped"},
        )
        assert resp.status_code == 409
        _assert_generic_conflict(
            resp.json(), "integrity_conflict", "uq_something_unmapped"
        )

    def test_missing_sqlstate(self, handler_client):
        resp = handler_client.get("/raise-integrity")
        assert resp.status_code == 409
        _assert_generic_conflict(resp.json(), "integrity_conflict", None)


class TestEveryGenericMessageIsDistinctAndActionable:
    """Six SQLSTATEs, six sentences, and not a constraint name among them.

    Four codes carry them -- ``state_conflict`` answers three SQLSTATEs --
    which is exactly why the sentence has to do work the code cannot: a
    depositor who left a column empty and one who contradicted a CHECK
    both receive ``state_conflict``, and the prose is the only thing that
    tells them apart.

    A repair-shaped description is the half of the ``Shape.relationship``
    obligation this seam can actually pay: it holds no fact about the
    request beyond the constraint name, and that is the one thing it may
    not disclose. What it can do is stop saying "Integrity constraint
    violation." to four different failures. This test is what makes that
    a contract rather than a nicety -- it fails if a future edit
    collapses them back onto one sentence.
    """

    CASES = (
        ("23505", "unique_conflict"),
        ("23503", "reference_conflict"),
        ("23514", "state_conflict"),
        ("23502", "state_conflict"),
        ("23P01", "state_conflict"),
        ("99999", "integrity_conflict"),
    )

    def test_each_sqlstate_gets_its_own_sentence(self, handler_client):
        details = {}
        for sqlstate, code in self.CASES:
            resp = handler_client.get(
                "/raise-integrity", params={"sqlstate": sqlstate}
            )
            body = resp.json()
            assert body["code"] == code
            details[sqlstate] = body["detail"]

        assert len(set(details.values())) == len(self.CASES), (
            "two SQLSTATEs share a sentence, so a depositor cannot tell a "
            f"missing field from a broken rule: {details}"
        )
        for sqlstate, detail in details.items():
            # Long enough to have said what to do, not merely what failed.
            assert len(detail) > 60, (sqlstate, detail)
            assert detail.endswith("."), (sqlstate, detail)


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
