"""API tests for the universal upload→submission model.

Every accepted ``/uploads/*`` call is a reviewable contribution: it creates a
``submission`` wrapper, links the produced scientific records to it, and
initialises their ``record_review`` rows at ``under_review`` — without
implying curator approval. These tests cover the computed-reaction and
remaining direct-upload kinds, transactional rollback on failure, and
idempotent-replay de-duplication.

Direct thermo / computed-species coverage lives in
``test_api_record_reviews.py``; this module covers the rest plus the
failure/idempotency invariants.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from sqlalchemy.orm import Session

from app.db.models.common import (
    RecordReviewStatus,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionStatus,
    UploadJobKind,
)
from app.db.models.record_review import RecordReview
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.db.models.upload_job import UploadJob

# Reuse ready-made, schema-valid payloads from the per-kind upload suites.
from tests.api.test_api_kfir_rxn import _BUNDLE as _COMPUTED_REACTION_BUNDLE
from tests.api.test_api_statmech_upload import _statmech_payload
from tests.api.test_api_transport_upload import _transport_payload

KEY_HEADER = "Idempotency-Key"


def _submission_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Submission)) or 0


def _links_for(db_session, submission_id: int) -> list[SubmissionRecordLink]:
    return list(
        db_session.scalars(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# computed-reaction (requirements 4–6)
# ---------------------------------------------------------------------------


class TestComputedReactionUploadSubmission:
    def test_creates_submission_links_and_under_review_rows(
        self, client, db_session
    ):
        before = _submission_count(db_session)
        resp = client.post(
            "/api/v1/uploads/computed-reaction", json=_COMPUTED_REACTION_BUNDLE
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        # (4) exactly one submission of the right kind.
        assert _submission_count(db_session) == before + 1
        submission_id = body["submission_id"]
        assert submission_id is not None
        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        assert submission.submission_kind is SubmissionKind.computed_reaction

        # (5) record links cover the reaction entry and its products.
        links = _links_for(db_session, submission_id)
        assert links, "computed-reaction upload should create record links"
        link_types = {link.record_type for link in links}
        assert SubmissionRecordType.reaction_entry in link_types
        # The TS and kinetics, when present, are linked too.
        assert body["reaction_entry_id"] in {
            link.record_id
            for link in links
            if link.record_type is SubmissionRecordType.reaction_entry
        }

        # (6) every linked record is under_review and points at the submission.
        for link in links:
            review = db_session.scalar(
                select(RecordReview).where(
                    RecordReview.record_type == link.record_type,
                    RecordReview.record_id == link.record_id,
                )
            )
            assert review is not None, f"missing review for {link.record_type}"
            assert review.status is RecordReviewStatus.under_review
            assert review.submission_id == submission_id


# ---------------------------------------------------------------------------
# Remaining direct kinds (requirement 7)
# ---------------------------------------------------------------------------


class TestDirectProductUploadsCreateSubmissions:
    @pytest.mark.parametrize(
        "path, payload_factory, kind, product_type",
        [
            (
                "/api/v1/uploads/statmech",
                _statmech_payload,
                SubmissionKind.statmech,
                SubmissionRecordType.statmech,
            ),
            (
                "/api/v1/uploads/transport",
                _transport_payload,
                SubmissionKind.transport,
                SubmissionRecordType.transport,
            ),
        ],
    )
    def test_upload_creates_submission_and_links_product(
        self, client, db_session, path, payload_factory, kind, product_type
    ):
        resp = client.post(path, json=payload_factory())
        assert resp.status_code == 201, resp.text
        body = resp.json()

        submission_id = body["submission_id"]
        assert submission_id is not None
        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        assert submission.submission_kind is kind

        product_id = body["id"]
        link = db_session.scalar(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id,
                SubmissionRecordLink.record_type == product_type,
                SubmissionRecordLink.record_id == product_id,
            )
        )
        assert link is not None

        review = db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type == product_type,
                RecordReview.record_id == product_id,
            )
        )
        assert review is not None
        assert review.status is RecordReviewStatus.under_review
        assert review.submission_id == submission_id


# ---------------------------------------------------------------------------
# Failure leaves no inconsistent state (requirement 8)
# ---------------------------------------------------------------------------


class TestFailedUploadRollsBackSubmission:
    def test_workflow_failure_leaves_no_submission(
        self, db_session, _api_test_user
    ):
        """A failure after submission creation must roll back the submission,
        its audit events, and any links together.

        Modelled at the service level inside an explicit savepoint — exactly
        what ``get_write_db`` does in production — because the TestClient
        fixture replaces ``get_write_db`` with a no-op session override, so
        this property cannot be exercised through the route (see the matching
        note in ``tests/services/test_contribution_bundle_submit.py``).
        """
        from app.services.upload_submission import open_upload_submission

        before = {
            "submission": _submission_count(db_session),
            "audit": db_session.scalar(
                select(func.count()).select_from(SubmissionAuditEvent)
            )
            or 0,
            "links": db_session.scalar(
                select(func.count()).select_from(SubmissionRecordLink)
            )
            or 0,
        }

        nested = db_session.begin_nested()
        try:
            with pytest.raises(RuntimeError):
                # Submission shell is created and flushed...
                open_upload_submission(
                    db_session,
                    created_by=_api_test_user,
                    kind=SubmissionKind.thermo,
                )
                # ...then the workflow fails before mark_upload_ingested.
                raise RuntimeError("simulated persistence failure")
            nested.rollback()
        finally:
            if nested.is_active:
                nested.rollback()

        after = {
            "submission": _submission_count(db_session),
            "audit": db_session.scalar(
                select(func.count()).select_from(SubmissionAuditEvent)
            )
            or 0,
            "links": db_session.scalar(
                select(func.count()).select_from(SubmissionRecordLink)
            )
            or 0,
        }
        assert before == after


# ---------------------------------------------------------------------------
# Idempotent replay does not duplicate submissions (requirement 9)
# ---------------------------------------------------------------------------


class TestIdempotentReplayDoesNotDuplicateSubmission:
    def test_replay_returns_same_submission_without_new_rows(
        self, client, db_session
    ):
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
        }
        headers = {KEY_HEADER: "thermo-submission-idem-key-001"}

        first = client.post("/api/v1/uploads/thermo", json=payload, headers=headers)
        assert first.status_code == 201, first.text
        first_submission_id = first.json()["submission_id"]
        assert first_submission_id is not None

        subs_after_first = _submission_count(db_session)
        links_after_first = len(_links_for(db_session, first_submission_id))

        # Replay with the same key + body returns the stored response.
        second = client.post(
            "/api/v1/uploads/thermo", json=payload, headers=headers
        )
        assert second.status_code == 201, second.text
        assert second.json()["submission_id"] == first_submission_id

        # No second submission, no duplicate links.
        assert _submission_count(db_session) == subs_after_first
        assert len(_links_for(db_session, first_submission_id)) == links_after_first


# ---------------------------------------------------------------------------
# Async enqueue creates the submission wrapper (requirement 1)
# ---------------------------------------------------------------------------


class TestAsyncEnqueueCreatesSubmission:
    def test_enqueue_creates_submission_linked_to_job(self, client, db_session):
        resp = client.post(
            "/api/v1/jobs/transport", json=_transport_payload()
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()

        submission_id = body["submission_id"]
        assert submission_id is not None
        job_id = body["job_id"]

        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        # The submission is linked to its async job and classified by kind.
        assert submission.upload_job_id == job_id
        assert submission.submission_kind is SubmissionKind.transport
        # Awaiting processing, not approved.
        assert submission.status is SubmissionStatus.pending

        # submission_created audit exists; no record links yet (worker not run).
        kinds = {
            e.event_kind
            for e in db_session.scalars(
                select(SubmissionAuditEvent).where(
                    SubmissionAuditEvent.submission_id == submission_id
                )
            ).all()
        }
        assert SubmissionAuditEventKind.submission_created in kinds
        assert not _links_for(db_session, submission_id)


# ---------------------------------------------------------------------------
# Sync durable failed-ingestion audit (requirement: Part 3)
# ---------------------------------------------------------------------------


class TestSyncFailedUploadAudit:
    def test_record_failed_upload_persists_failed_submission(
        self, db_engine, _api_test_user
    ):
        """A failed synchronous upload durably records a ``failed`` submission
        with an ``ingestion_failed`` audit event and no scientific records,
        links, or review rows — in its own committed transaction.
        """
        from app.services.upload_submission import record_failed_upload

        submission_id = record_failed_upload(
            created_by=_api_test_user,
            kind=SubmissionKind.thermo,
            error_summary="ValueError: simulated parse/persist failure",
            session_factory=lambda: Session(bind=db_engine),
        )
        assert submission_id is not None

        try:
            with Session(db_engine) as s:
                submission = s.get(Submission, submission_id)
                assert submission is not None
                assert submission.status is SubmissionStatus.failed
                assert submission.submission_kind is SubmissionKind.thermo
                assert submission.created_by == _api_test_user

                kinds = {
                    e.event_kind
                    for e in s.scalars(
                        select(SubmissionAuditEvent).where(
                            SubmissionAuditEvent.submission_id == submission_id
                        )
                    ).all()
                }
                assert SubmissionAuditEventKind.submission_created in kinds
                assert SubmissionAuditEventKind.ingestion_failed in kinds

                # No scientific record links or review rows for a failed attempt.
                assert (
                    s.scalar(
                        select(func.count())
                        .select_from(SubmissionRecordLink)
                        .where(SubmissionRecordLink.submission_id == submission_id)
                    )
                    or 0
                ) == 0
                assert (
                    s.scalar(
                        select(func.count())
                        .select_from(RecordReview)
                        .where(RecordReview.submission_id == submission_id)
                    )
                    or 0
                ) == 0
        finally:
            with Session(db_engine) as s:
                with s.begin():
                    s.execute(
                        SubmissionAuditEvent.__table__.delete().where(
                            SubmissionAuditEvent.submission_id == submission_id
                        )
                    )
                    s.execute(
                        Submission.__table__.delete().where(
                            Submission.id == submission_id
                        )
                    )
