"""End-to-end: a failing idempotency receipt must not destroy the upload.

This is the 2026-08-05 incident reproduced at the level it happened —
a real upload route, the real workflow, and a write session that really
commits — with the receipt forced to fail. On the pre-fix code this exact
shape returned ``201 Created`` with a complete body (``species_entry_id``,
``conformer_group_id``, ``calculation_id`` all populated) while storing
**zero** rows, and raised nothing at the client.

The forcing function is deliberately not an encoding problem: the receipt
body carries a NUL character, which Postgres rejects in ``jsonb`` under
every server encoding (SQLSTATE 22P05). The original trigger was a U+2014
em-dash against a ``SQL_ASCII`` database and that cause is fixed, but the
shape — any commit-time failure confined to the receipt — is not
encoding-specific and is what these tests hold down.

The load-bearing difference from the rest of the API suite is that the
write session here **commits**; the shared ``client`` fixture never does,
and the payload loss happens at commit. See ``committed_api_client``.
Complementary tests in
``tests/services/test_idempotency_write_isolation.py`` drive the same
guarantee through a genuine top-level ``COMMIT`` and verify from a
brand-new session.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_current_user, get_db, get_write_db
from app.db.models.app_user import AppUser
from app.db.models.idempotency import IdempotencyRecord
from app.db.models.species import ConformerObservation
from app.services.idempotency import (
    IDEMPOTENCY_RECEIPT_FAILED,
    IDEMPOTENCY_RECEIPT_HEADER,
    IDEMPOTENCY_RECEIPT_REASON_HEADER,
    IDEMPOTENCY_RECEIPT_RECORDED,
)

CONFORMER_ENDPOINT = "/api/v1/uploads/conformers"
KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotency-Replayed"

#: A receipt body Postgres refuses to store in ``jsonb`` in any encoding.
UNSTORABLE_RECEIPT_BODY = {"poisoned": "unstorable" + chr(0) + "receipt"}


def _conformer_payload(label: str = "isolation-conf") -> dict:
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": label,
        "note": "receipt isolation test",
    }


def _poison_receipt_body(monkeypatch) -> None:
    """Force the receipt INSERT to fail, leaving the payload path untouched.

    Wraps the record constructor rather than raising from it, so the failure
    lands where the incident's did: inside the database, on the receipt's
    own INSERT, at the moment the unit of work is written out.
    """
    import app.services.idempotency as svc

    real = svc.record_response

    def poisoned(session, **kwargs):
        kwargs["response_body"] = UNSTORABLE_RECEIPT_BODY
        return real(session, **kwargs)

    monkeypatch.setattr(svc, "record_response", poisoned)


@pytest.fixture
def committed_api_client(db_engine, _api_test_user):
    """A ``TestClient`` whose write session commits, as production's does.

    The shared ``client`` fixture overrides ``get_write_db`` with a session
    that is never committed. That is the right default for the suite, but
    it cannot show this bug: the payload loss happens *at commit*, so a
    harness that never commits reports success either way.

    Here ``get_write_db`` gets the production contract — commit on success,
    roll back on error — over a session in ``create_savepoint`` join mode on
    a connection wrapped in an outer transaction. ``session.commit()`` runs
    the real flush-and-commit machinery and makes the rows readable, while
    teardown discards everything with a single rollback. Row-by-row cleanup
    is not an option: several tables here carry ``append-only`` database
    triggers that refuse ``DELETE`` outright.
    """
    app = create_app()

    connection = db_engine.connect()
    outer_transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    def committing_write_db():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    user = session.get(AppUser, _api_test_user)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_write_db] = committing_write_db
    app.dependency_overrides[get_current_user] = lambda: user

    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client._db_session = session
        yield test_client

    session.close()
    outer_transaction.rollback()
    connection.close()


def _count(session: Session, model) -> int:
    """Count rows with a real SELECT, not from the identity map."""
    return session.scalar(select(func.count()).select_from(model)) or 0


# ---------------------------------------------------------------------------
# 1. The regression itself
# ---------------------------------------------------------------------------


class TestPayloadSurvivesReceiptFailure:
    def test_upload_is_stored_even_when_the_receipt_cannot_be_written(
        self, committed_api_client, monkeypatch
    ) -> None:
        """The science must be in the database after the commit.

        The pre-fix code passed every "no exception propagated" check — it
        returned 201 and raised nothing at the client. What it did not do
        was store anything. So this asserts against the database after the
        write session has committed, which is the only assertion the old
        behaviour could not satisfy.
        """
        session = committed_api_client._db_session
        _poison_receipt_body(monkeypatch)
        before = _count(session, ConformerObservation)

        resp = committed_api_client.post(
            CONFORMER_ENDPOINT,
            json=_conformer_payload(),
            headers={KEY_HEADER: "isolation-key-aaaaaaaaaaa"},
        )

        assert resp.status_code == 201, resp.text
        after = _count(session, ConformerObservation)
        assert after == before + 1, (
            "the upload was destroyed by a failing idempotency receipt: "
            f"{after - before} conformer rows committed, expected 1"
        )

        # The body's claims must be true of the database, not just of JSON.
        body = resp.json()
        observation = session.get(ConformerObservation, body["id"])
        assert observation is not None
        assert observation.conformer_group_id == body["conformer_group_id"]

    def test_failed_receipt_is_not_left_behind(
        self, committed_api_client, monkeypatch
    ) -> None:
        """No half-written receipt: the savepoint rollback is complete."""
        session = committed_api_client._db_session
        _poison_receipt_body(monkeypatch)
        key = "isolation-noreceipt-aaaaa"

        resp = committed_api_client.post(
            CONFORMER_ENDPOINT,
            json=_conformer_payload("noreceipt-conf"),
            headers={KEY_HEADER: key},
        )
        assert resp.status_code == 201, resp.text

        assert session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == key
            )
        ) is None


# ---------------------------------------------------------------------------
# 2. The failure is visible
# ---------------------------------------------------------------------------


class TestReceiptFailureIsVisible:
    def test_response_says_the_receipt_failed(
        self, committed_api_client, monkeypatch
    ) -> None:
        """201 is truthful about the science; the header is truthful about retries.

        Returning a bare 201 is the pre-fix behaviour and is what made the
        incident invisible. Failing the request instead would be worse: the
        write really did succeed, and a 5xx invites a retry that — with no
        receipt on file — re-executes the upload and duplicates the science.
        """
        _poison_receipt_body(monkeypatch)

        resp = committed_api_client.post(
            CONFORMER_ENDPOINT,
            json=_conformer_payload("visible-conf"),
            headers={KEY_HEADER: "isolation-visible-aaaaaa"},
        )

        assert resp.status_code == 201, resp.text
        assert resp.headers.get(IDEMPOTENCY_RECEIPT_HEADER) == (
            IDEMPOTENCY_RECEIPT_FAILED
        )
        assert resp.headers.get(IDEMPOTENCY_RECEIPT_REASON_HEADER)

    def test_server_logs_the_failure_loudly(
        self, committed_api_client, monkeypatch, caplog
    ) -> None:
        """The incident left a bare access-log line; diagnosis needed the source."""
        _poison_receipt_body(monkeypatch)
        key = "isolation-logged-aaaaaaaa"

        with caplog.at_level(logging.ERROR):
            resp = committed_api_client.post(
                CONFORMER_ENDPOINT,
                json=_conformer_payload("logged-conf"),
                headers={KEY_HEADER: key},
            )
        assert resp.status_code == 201, resp.text

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "a receipt failure must be logged at ERROR"
        joined = " ".join(r.getMessage() for r in errors)
        assert key in joined, "the log must name the affected idempotency key"
        assert CONFORMER_ENDPOINT in joined, "the log must name the endpoint"


class TestTransientFailureSelfHeals:
    def test_receipt_is_recovered_after_the_payload_commits(
        self, db_engine, committed_api_client, monkeypatch
    ) -> None:
        """A transient receipt failure must not break the key permanently.

        The first attempt fails inside the savepoint; the payload commits
        anyway; the post-commit retry then writes the receipt in its own
        transaction and idempotency for the key is restored. The response
        header still reads ``failed`` because it is generated before the
        payload is durable, and claiming success there would be a guess.
        """
        import app.services.idempotency as svc

        real = svc.record_response
        attempts = {"n": 0}

        def flaky(session, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                kwargs["response_body"] = UNSTORABLE_RECEIPT_BODY
            return real(session, **kwargs)

        monkeypatch.setattr(svc, "record_response", flaky)
        key = "isolation-selfheal-aaaaaa"

        try:
            resp = committed_api_client.post(
                CONFORMER_ENDPOINT,
                json=_conformer_payload("selfheal-conf"),
                headers={KEY_HEADER: key},
            )
            assert resp.status_code == 201, resp.text
            assert resp.headers.get(IDEMPOTENCY_RECEIPT_HEADER) == (
                IDEMPOTENCY_RECEIPT_FAILED
            )
            assert attempts["n"] == 2, "the receipt should have been retried once"

            # The retry ran on its own connection, so it is visible to a
            # session that shares nothing with the request.
            with Session(db_engine) as probe:
                assert probe.scalar(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.idempotency_key == key
                    )
                ) is not None
        finally:
            # The recovered receipt was committed outside the test's
            # transaction by design, so it needs removing by hand.
            with Session(db_engine) as cleanup:
                cleanup.execute(
                    delete(IdempotencyRecord).where(
                        IdempotencyRecord.idempotency_key == key
                    )
                )
                cleanup.commit()


# ---------------------------------------------------------------------------
# 3. The normal path is unchanged
# ---------------------------------------------------------------------------


class TestNormalPathUnchanged:
    def test_receipt_recorded_header_on_success(self, client) -> None:
        resp = client.post(
            CONFORMER_ENDPOINT,
            json=_conformer_payload("normal-conf"),
            headers={KEY_HEADER: "isolation-normal-aaaaaaa"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.headers.get(IDEMPOTENCY_RECEIPT_HEADER) == (
            IDEMPOTENCY_RECEIPT_RECORDED
        )

    def test_second_identical_request_replays_rather_than_re_executes(
        self, client, db_session
    ) -> None:
        """The replay contract is untouched by the isolation change."""
        payload = _conformer_payload("replay-conf")
        key = "isolation-replay-aaaaaaa"

        first = client.post(CONFORMER_ENDPOINT, json=payload, headers={KEY_HEADER: key})
        assert first.status_code == 201, first.text
        assert REPLAYED_HEADER not in first.headers

        before = _count(db_session, ConformerObservation)
        second = client.post(CONFORMER_ENDPOINT, json=payload, headers={KEY_HEADER: key})

        assert second.status_code == 201
        assert second.headers.get(REPLAYED_HEADER) == "true"
        assert second.json() == first.json()
        assert _count(db_session, ConformerObservation) == before, (
            "replay must not re-execute the write"
        )

    def test_upload_and_receipt_both_committed_on_the_happy_path(
        self, committed_api_client
    ) -> None:
        """Both rows land in one commit when nothing is forced to fail."""
        session = committed_api_client._db_session
        key = "isolation-happy-aaaaaaaa"
        before = _count(session, ConformerObservation)

        resp = committed_api_client.post(
            CONFORMER_ENDPOINT,
            json=_conformer_payload("happy-conf"),
            headers={KEY_HEADER: key},
        )
        assert resp.status_code == 201, resp.text
        assert resp.headers.get(IDEMPOTENCY_RECEIPT_HEADER) == (
            IDEMPOTENCY_RECEIPT_RECORDED
        )
        assert _count(session, ConformerObservation) == before + 1

        stored = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == key
            )
        )
        assert stored is not None
        assert stored.status_code == 201

    def test_replay_still_works_after_a_committing_write(
        self, committed_api_client
    ) -> None:
        """Committed receipt, then a replay — the full round trip."""
        payload = _conformer_payload("commit-replay-conf")
        key = "isolation-creplay-aaaaaa"

        first = committed_api_client.post(
            CONFORMER_ENDPOINT, json=payload, headers={KEY_HEADER: key}
        )
        assert first.status_code == 201, first.text

        session = committed_api_client._db_session
        before = _count(session, ConformerObservation)
        second = committed_api_client.post(
            CONFORMER_ENDPOINT, json=payload, headers={KEY_HEADER: key}
        )

        assert second.status_code == 201
        assert second.headers.get(REPLAYED_HEADER) == "true"
        assert second.json() == first.json()
        assert _count(session, ConformerObservation) == before


# ---------------------------------------------------------------------------
# 4. A concurrent duplicate is still a conflict, not a "failed receipt"
# ---------------------------------------------------------------------------


class TestConcurrentDuplicateStillConflicts:
    def test_unique_violation_returns_409_and_stores_nothing(
        self, committed_api_client, monkeypatch
    ) -> None:
        """Isolating the receipt must not downgrade the race to ``201``.

        Two in-flight requests sharing a key are caught by the uniqueness
        scope at write time. If that violation were swallowed as a "receipt
        failure", both racers would commit their science under one key —
        the exact outcome idempotency exists to prevent. The pre-lookup is
        patched out to force the collision at write time, which is what a
        genuine race does.
        """
        from app.api import idempotency as api_idem

        session = committed_api_client._db_session
        key = "isolation-duplicate-aaaaa"
        payload = _conformer_payload("duplicate-conf")

        # A committed receipt already occupies the uniqueness scope.
        first = committed_api_client.post(
            CONFORMER_ENDPOINT, json=payload, headers={KEY_HEADER: key}
        )
        assert first.status_code == 201, first.text

        # Simulate the race: the lookup misses, so the collision surfaces
        # on the receipt INSERT exactly as it would for two live requests.
        monkeypatch.setattr(api_idem, "lookup_or_conflict", lambda *a, **k: None)
        before = _count(session, ConformerObservation)

        second = committed_api_client.post(
            CONFORMER_ENDPOINT, json=payload, headers={KEY_HEADER: key}
        )

        assert second.status_code == 409, second.text
        assert second.json()["code"] == "idempotency_conflict"
        assert _count(session, ConformerObservation) == before, (
            "a losing racer must not leave science behind"
        )
