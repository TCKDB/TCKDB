"""The idempotency receipt must never be able to destroy the payload it receipts for.

Regression tests for the 2026-08-05 incident: an upload completed, ``201
Created`` was determined, and then the idempotency row failed to serialise
at commit time. The receipt shared the upload's transaction, so the whole
unit of work rolled back — species, calculation, everything — while the
client had already been told ``uploaded: 1``.

The original trigger was an encoding mismatch (a U+2014 em-dash against a
``SQL_ASCII`` database) and that specific cause has been fixed by
converting the database to UTF-8. The *shape* is encoding-independent, so
these tests reproduce it with a value no Postgres encoding will accept in
``jsonb``: a NUL character. ``\\u0000`` is rejected by ``jsonb`` in every
server encoding (SQLSTATE 22P05), which makes it a stable, portable stand-in
for "the receipt body is unstorable".

The load-bearing assertion in every test here is made from a **fresh
session after a genuine top-level commit** — not merely "no exception
propagated". The incident propagated no exception either.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.common import MoleculeKind, StereoKind
from app.db.models.idempotency import IdempotencyRecord
from app.db.models.species import Species
from app.services.idempotency import (
    IDEMPOTENCY_TTL,
    IDEMPOTENCY_UNIQUE_CONSTRAINT,
    ReceiptWriteFailed,
    canonical_payload_hash,
    record_response,
    retry_receipt_after_commit,
    write_receipt_isolated,
)

ENDPOINT = "/api/v1/uploads/conformers"


def _naive_utcnow() -> datetime:
    """Match the tz-naive DateTime columns this module reads and writes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# A receipt body Postgres will refuse to store in ``jsonb`` regardless of
# server encoding. This is the encoding-independent stand-in for the
# em-dash that caused the incident.
UNSTORABLE_BODY = {"type": "conformer_observation", "note": "bad" + chr(0) + "value"}
STORABLE_BODY = {"type": "conformer_observation", "id": 1}


def _species_kwargs(marker: str) -> dict:
    """A minimal but real science row — the 'payload' these tests protect."""
    return {
        "kind": MoleculeKind.molecule,
        "smiles": f"[He]{marker}",
        "inchi_key": "SWQJXJOGLNCZEY" + "-UHFFFAOYSA-N",
        "charge": 0,
        "multiplicity": 1,
        "stereo_kind": StereoKind.unspecified,
    }


def _receipt_kwargs(user_id: int, key: str) -> dict:
    return {
        "user_id": user_id,
        "request_method": "POST",
        "endpoint": ENDPOINT,
        "idempotency_key": key,
        "payload_hash": canonical_payload_hash({"marker": key}),
        "status_code": 201,
    }


@pytest.fixture
def committed_scratch(db_engine, _api_test_user):
    """Give a test a real, committing session plus guaranteed cleanup.

    Every other fixture in the suite isolates by rolling a transaction
    back, which is precisely the thing under test here: a rollback would
    hide the bug rather than expose it. So these tests commit for real and
    clean up after themselves by marker.

    Yields ``(user_id, marker)``; deletes every row carrying ``marker`` on
    the way out, whether the test passed or failed.
    """
    marker = "recisol"
    keys: list[str] = []

    def _register(key: str) -> str:
        keys.append(key)
        return key

    try:
        yield _api_test_user, marker, _register
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(
                delete(Species).where(Species.smiles.like(f"[He]{marker}%"))
            )
            if keys:
                cleanup.execute(
                    delete(IdempotencyRecord).where(
                        IdempotencyRecord.idempotency_key.in_(keys)
                    )
                )
            cleanup.commit()


class TestPayloadSurvivesReceiptFailure:
    """The regression that matters: a bad receipt must not take the science with it."""

    def test_payload_is_present_after_commit_when_the_receipt_cannot_be_written(
        self, db_engine, committed_scratch
    ) -> None:
        """Reproduces the incident shape and asserts the payload survives.

        On the pre-fix code the receipt shares the payload's transaction,
        the commit fails, and the species row is gone. The assertion is
        made from a *separate session*, so it reads committed state and
        cannot be satisfied by objects still sitting in an identity map.
        """
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-payload-survives-01")
        smiles = _species_kwargs(f"{marker}-a")["smiles"]

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs(f"{marker}-a")))

            with pytest.raises(ReceiptWriteFailed):
                write_receipt_isolated(
                    session,
                    **_receipt_kwargs(user_id, key),
                    response_body=UNSTORABLE_BODY,
                )

            # The receipt failed; the payload must still commit cleanly.
            session.commit()

        with Session(db_engine) as verify:
            stored = verify.scalar(select(Species).where(Species.smiles == smiles))
            assert stored is not None, (
                "the upload payload was destroyed by a failing idempotency receipt"
            )
            assert verify.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == key
                )
            ) is None, "a receipt that failed to write must not be left behind"

    def test_session_stays_usable_after_a_receipt_failure(
        self, db_engine, committed_scratch
    ) -> None:
        """A failed receipt rolls back to its SAVEPOINT, not the transaction.

        The route continues after ``idem.record`` returns, so the session
        must still accept writes; a poisoned transaction would surface as
        ``PendingRollbackError`` on the next flush.
        """
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-still-usable-02")

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs(f"{marker}-b")))
            session.flush()

            with pytest.raises(ReceiptWriteFailed):
                write_receipt_isolated(
                    session,
                    **_receipt_kwargs(user_id, key),
                    response_body=UNSTORABLE_BODY,
                )

            # Still usable: more payload can be written after the failure.
            session.add(Species(**_species_kwargs(f"{marker}-c")))
            session.commit()

        with Session(db_engine) as verify:
            found = verify.scalars(
                select(Species).where(Species.smiles.like(f"[He]{marker}-%"))
            ).all()
            assert {s.smiles for s in found} == {
                f"[He]{marker}-b",
                f"[He]{marker}-c",
            }

    def test_payload_flushed_before_the_savepoint_is_not_rolled_back(
        self, db_engine, committed_scratch
    ) -> None:
        """Unflushed payload must not be swept into the receipt's SAVEPOINT.

        ``write_receipt_isolated`` flushes the session *before* opening the
        savepoint. Without that, any payload still pending at receipt time
        would be INSERTed inside the savepoint and rolled back with it —
        the same data loss through a subtler door.
        """
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-pending-payload-03")

        with Session(db_engine) as session:
            # Deliberately NOT flushed: still pending when the receipt runs.
            session.add(Species(**_species_kwargs(f"{marker}-d")))

            with pytest.raises(ReceiptWriteFailed):
                write_receipt_isolated(
                    session,
                    **_receipt_kwargs(user_id, key),
                    response_body=UNSTORABLE_BODY,
                )
            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{marker}-d")
            ) is not None


class TestReceiptFailureIsLoud:
    def test_failure_carries_a_reason(self, db_engine, committed_scratch) -> None:
        """The raised error names the cause; diagnosis must not require reading source."""
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-reason-04")

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs(f"{marker}-e")))
            with pytest.raises(ReceiptWriteFailed) as excinfo:
                write_receipt_isolated(
                    session,
                    **_receipt_kwargs(user_id, key),
                    response_body=UNSTORABLE_BODY,
                )
            session.rollback()

        assert excinfo.value.reason
        assert excinfo.value.__cause__ is not None

    def test_service_logs_the_failure_at_error(
        self, db_engine, committed_scratch, caplog
    ) -> None:
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-logged-05")

        with caplog.at_level(logging.ERROR, logger="app.services.idempotency"):
            with Session(db_engine) as session:
                with pytest.raises(ReceiptWriteFailed):
                    write_receipt_isolated(
                        session,
                        **_receipt_kwargs(user_id, key),
                        response_body=UNSTORABLE_BODY,
                    )
                session.rollback()

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "a receipt failure must be logged loudly by the server"
        joined = " ".join(r.getMessage() for r in errors)
        assert key in joined
        assert ENDPOINT in joined


class TestNormalPathUnchanged:
    def test_receipt_and_payload_commit_together(
        self, db_engine, committed_scratch
    ) -> None:
        """The success path is still atomic: one commit, both rows."""
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-normal-06")

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs(f"{marker}-f")))
            record = write_receipt_isolated(
                session,
                **_receipt_kwargs(user_id, key),
                response_body=STORABLE_BODY,
            )
            assert record is not None
            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{marker}-f")
            ) is not None
            stored = verify.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == key
                )
            )
            assert stored is not None
            assert stored.status_code == 201
            assert stored.response_body_json == STORABLE_BODY

    def test_payload_rollback_still_discards_the_receipt(
        self, db_engine, committed_scratch
    ) -> None:
        """The original contract holds: a rolled-back write leaves no receipt.

        Releasing the savepoint does not make the receipt durable on its
        own — it is still inside the caller's transaction, so a retryable
        failure after ``record`` discards it exactly as before.
        """
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-rollback-07")

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs(f"{marker}-g")))
            write_receipt_isolated(
                session,
                **_receipt_kwargs(user_id, key),
                response_body=STORABLE_BODY,
            )
            session.rollback()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == key
                )
            ) is None
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{marker}-g")
            ) is None


class TestExpiredReceiptInScope:
    """An expired key is documented to behave as if never used.

    The uniqueness scope has no notion of expiry, so the new receipt used
    to collide with the expired row still sitting on disk — surfacing at
    ``COMMIT`` as a ``409`` that rolled the upload back, whenever the
    unscheduled ``delete_expired_records`` sweep had not run.
    """

    def test_expired_record_is_replaced(
        self, db_engine, committed_scratch
    ) -> None:
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-expired-12")

        with Session(db_engine) as seed:
            record_response(
                seed,
                **_receipt_kwargs(user_id, key),
                response_body={"stale": True},
                now=_naive_utcnow() - IDEMPOTENCY_TTL - timedelta(days=1),
            )
            seed.commit()

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs(f"{marker}-j")))
            write_receipt_isolated(
                session,
                **_receipt_kwargs(user_id, key),
                response_body=STORABLE_BODY,
            )
            session.commit()

        with Session(db_engine) as verify:
            records = verify.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == key
                )
            ).all()
            assert len(records) == 1
            assert records[0].response_body_json == STORABLE_BODY

    def test_live_record_in_scope_still_conflicts(
        self, db_engine, committed_scratch
    ) -> None:
        """Only the expired row is cleared — a live one is still a 409."""
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-live-13")

        with Session(db_engine) as seed:
            record_response(
                seed, **_receipt_kwargs(user_id, key), response_body=STORABLE_BODY
            )
            seed.commit()

        with Session(db_engine) as session:
            with pytest.raises(IntegrityError):
                write_receipt_isolated(
                    session,
                    **_receipt_kwargs(user_id, key),
                    response_body=STORABLE_BODY,
                )
            session.rollback()


class TestReceiptRetry:
    """A failed receipt is retryable once the payload is durable.

    The savepoint keeps the payload safe but leaves idempotency broken for
    that key: a retry would re-execute rather than replay. Re-attempting the
    receipt in its own transaction — only ever *after* the payload commits —
    turns a transient failure into a recoverable one.
    """

    def test_retry_writes_the_receipt_in_a_separate_transaction(
        self, db_engine, committed_scratch
    ) -> None:
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-retry-09")

        recovered = retry_receipt_after_commit(
            lambda: Session(db_engine),
            **_receipt_kwargs(user_id, key),
            response_body=STORABLE_BODY,
        )

        assert recovered is True
        with Session(db_engine) as verify:
            stored = verify.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == key
                )
            )
            assert stored is not None
            assert stored.response_body_json == STORABLE_BODY

    def test_retry_reports_failure_without_raising(
        self, db_engine, committed_scratch, caplog
    ) -> None:
        """A permanently-unstorable receipt must not raise into the commit path.

        This runs after the response has been sent; anything escaping here
        would surface as an unhandled error on a request that already
        succeeded.
        """
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-retry-fails-10")

        with caplog.at_level(logging.ERROR, logger="app.services.idempotency"):
            recovered = retry_receipt_after_commit(
                lambda: Session(db_engine),
                **_receipt_kwargs(user_id, key),
                response_body=UNSTORABLE_BODY,
            )

        assert recovered is False
        joined = " ".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
        )
        assert key in joined

    def test_retry_does_not_join_the_callers_transaction(
        self, db_engine, committed_scratch
    ) -> None:
        """The retry's own rollback must not be able to reach the payload.

        A retry session sharing the caller's connection would share its
        transaction, and rolling back a failed retry could roll back the
        write it exists to protect — the original bug, reintroduced through
        the repair. Here the caller's transaction is left uncommitted and
        the retry runs anyway; the caller's rows must still be pending, not
        destroyed, when it finally commits.
        """
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-retry-isolated-11")

        with Session(db_engine) as caller:
            caller.add(Species(**_species_kwargs(f"{marker}-i")))
            caller.flush()

            retry_receipt_after_commit(
                lambda: Session(db_engine),
                **_receipt_kwargs(user_id, key),
                response_body=STORABLE_BODY,
            )

            caller.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{marker}-i")
            ) is not None


class TestConcurrentDuplicateStillConflicts:
    """A duplicate key is a conflict, not a receipt malfunction.

    Before this change a concurrent duplicate surfaced as a unique-constraint
    violation at commit, which rolled the write back and returned ``409``.
    Isolating the receipt must not downgrade that into "receipt failed, 201" —
    that would let both racers store their science under one key.
    """

    def test_unique_violation_propagates_and_aborts_the_write(
        self, db_engine, committed_scratch
    ) -> None:
        user_id, marker, register = committed_scratch
        key = register(f"{marker}-duplicate-08")

        # Pre-existing committed receipt for the same scope.
        with Session(db_engine) as seed:
            record_response(
                seed, **_receipt_kwargs(user_id, key), response_body=STORABLE_BODY
            )
            seed.commit()

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs(f"{marker}-h")))
            with pytest.raises(IntegrityError) as excinfo:
                write_receipt_isolated(
                    session,
                    **_receipt_kwargs(user_id, key),
                    response_body=STORABLE_BODY,
                )
            constraint = getattr(
                getattr(excinfo.value.orig, "diag", None), "constraint_name", None
            )
            assert constraint == IDEMPOTENCY_UNIQUE_CONSTRAINT
            # The route's error path rolls back — no half-stored science.
            session.rollback()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{marker}-h")
            ) is None
