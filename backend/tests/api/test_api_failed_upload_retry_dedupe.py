"""A refused retry under one ``Idempotency-Key`` is one failed submission.

Before this, ``record_failed_upload`` opened a fresh ``failed`` submission (plus
two audit events) for every refused attempt, ignoring the idempotency key, so a
producer retrying a refused deposit grew the table by three rows per retry.

The rule under test:

* keyed retries (same credential, same route, same key) share one failed
  submission, and every attempt still leaves its own ``ingestion_failed`` event
  (the audit trail is appended to, never updated, and never dropped);
* a different key, a different credential or a different route is a different
  contribution event and gets its own submission;
* no key means no deduplication (see ``record_failed_upload`` for why).

The failed-audit writer runs on the ambient ``SessionLocal``; the autouse
``_isolate_out_of_request_sessions`` fixture binds it to a connection that is
rolled back at teardown, so reading through ``SessionLocal`` sees the audit rows
and nothing is left committed.
"""

from __future__ import annotations

from sqlalchemy import select

from app.api import deps as api_deps
from app.db.models.common import (
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionStatus,
)
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.upload_submission import FailedUploadKey, record_failed_upload

KEY_HEADER = "Idempotency-Key"

# Parses, so it reaches the route body, then fails in the workflow.
_BAD_THERMO = {
    "enthalpy_reference_kind": "formation_298k",
    "species_entry": {"smiles": "this is not a smiles", "charge": 0, "multiplicity": 1},
    "scientific_origin": "computed",
    "h298_kj_mol": 1.0,
}


def _failed_thermo_submissions() -> list[Submission]:
    with api_deps.SessionLocal() as s:
        return list(
            s.scalars(
                select(Submission).where(
                    Submission.status == SubmissionStatus.failed,
                    Submission.submission_kind == SubmissionKind.thermo,
                )
            ).all()
        )


def _failed_events(submission_id: int) -> list[SubmissionAuditEvent]:
    with api_deps.SessionLocal() as s:
        return list(
            s.scalars(
                select(SubmissionAuditEvent)
                .where(
                    SubmissionAuditEvent.submission_id == submission_id,
                    SubmissionAuditEvent.event_kind
                    == SubmissionAuditEventKind.ingestion_failed,
                )
                .order_by(SubmissionAuditEvent.id)
            ).all()
        )


def _post(client, key: str | None):
    headers = {KEY_HEADER: key} if key else {}
    return client.post("/api/v1/uploads/thermo", json=_BAD_THERMO, headers=headers)


def test_keyed_retries_share_one_failed_submission(client, _api_test_user) -> None:
    before = len(_failed_thermo_submissions())

    for _ in range(3):
        resp = _post(client, "failed-retry-dedupe-key-001")
        assert resp.status_code >= 400, resp.text

    rows = _failed_thermo_submissions()
    assert len(rows) == before + 1, "three refused retries must open one submission"

    mine = [
        e
        for r in rows
        for e in _failed_events(r.id)
        if (e.details_json or {}).get("idempotency_key") == "failed-retry-dedupe-key-001"
    ]
    # The audit trail is kept: one ingestion_failed event per attempt.
    assert [e.details_json["attempt"] for e in mine] == [1, 2, 3]
    assert len({e.submission_id for e in mine}) == 1
    assert all(e.details_json["route"] == "POST /api/v1/uploads/thermo" for e in mine)
    assert all(e.reason for e in mine)


def test_a_different_key_is_a_different_submission(client, _api_test_user) -> None:
    before = len(_failed_thermo_submissions())
    _post(client, "failed-retry-dedupe-key-aaa")
    _post(client, "failed-retry-dedupe-key-bbb")
    assert len(_failed_thermo_submissions()) == before + 2


def test_unkeyed_failures_are_not_deduplicated(client, _api_test_user) -> None:
    before = len(_failed_thermo_submissions())
    _post(client, None)
    _post(client, None)
    assert len(_failed_thermo_submissions()) == before + 2


def test_another_credential_under_the_same_key_is_not_merged(
    db_engine, _api_test_user
) -> None:
    """Scope is (credential, route, key): the same key string from another user
    must never land on, or reveal, someone else's failed submission."""
    from sqlalchemy.orm import Session

    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole

    key = FailedUploadKey(route="POST /api/v1/uploads/thermo", idempotency_key="k" * 20)
    with api_deps.SessionLocal() as s:
        other = AppUser(username="dedupe-other-user", role=AppUserRole.user)
        s.add(other)
        s.flush()
        other_id = other.id
        s.commit()

    def factory() -> Session:
        return api_deps.SessionLocal()

    first = record_failed_upload(
        created_by=_api_test_user,
        kind=SubmissionKind.thermo,
        error_summary="boom",
        session_factory=factory,
        retry_key=key,
    )
    second = record_failed_upload(
        created_by=other_id,
        kind=SubmissionKind.thermo,
        error_summary="boom",
        session_factory=factory,
        retry_key=key,
    )
    again = record_failed_upload(
        created_by=_api_test_user,
        kind=SubmissionKind.thermo,
        error_summary="boom again",
        session_factory=factory,
        retry_key=key,
    )
    assert first is not None and second is not None
    assert first != second
    assert again == first
