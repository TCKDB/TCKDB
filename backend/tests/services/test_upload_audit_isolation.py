"""Upload bookkeeping must not be able to veto the upload.

Two failure paths in the synchronous ``/uploads/*`` flow, both about audit
rows rather than science:

* :func:`mark_upload_ingested` appends the ``ingestion_succeeded`` event on
  the line immediately above ``idem.record`` — the write that destroyed an
  upload on 2026-08-05. It carries the same two column types (``Text``
  ``summary``, ``JSONB`` ``details_json``) and, before this, the same total
  absence of isolation. Its failure must cost one line of audit history and
  never a scientific record.

* :func:`audit_sync_upload_failure` could not see a commit-time failure at
  all, because ``get_write_db`` commits in dependency teardown *after* the
  route returns. That left no failed-upload audit row for the one failure
  class where the response was already determined and the work is gone.

Both are asserted from a **fresh session after a genuine top-level commit**.
A NUL character is the encoding-independent stand-in for "this value cannot
be stored": PostgreSQL rejects ``\\u0000`` in ``text`` and ``jsonb`` in every
server encoding.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.api import deps as api_deps
from app.api.deps import get_write_db
from app.db.models.common import (
    MoleculeKind,
    StereoKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionStatus,
)
from app.db.models.species import Species
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.upload_submission import (
    SYNC_UPLOAD_AUDIT_KEY,
    audit_upload_failure_at_commit,
    mark_upload_ingested,
    open_upload_submission,
)

MARKER = "auditisol"

#: Rejected by ``text`` and ``jsonb`` alike, in every server encoding.
UNSTORABLE = "ingested" + chr(0) + "summary"


def _species_kwargs(suffix: str) -> dict:
    return {
        "kind": MoleculeKind.molecule,
        "smiles": f"[He]{MARKER}{suffix}",
        "inchi_key": "SWQJXJOGLNCZEY-UHFFFAOYSA-N",
        "charge": 0,
        "multiplicity": 1,
        "stereo_kind": StereoKind.unspecified,
    }


@pytest.fixture
def committed_scratch(db_engine, _api_test_user):
    """A committing scratch space; these tests cannot isolate by rollback.

    Rolling back is the bug under test, so the payload has to reach a real
    ``COMMIT`` before it is believed. Every submission committed while the
    test runs — including the ones ``record_failed_upload`` writes under its
    own server-chosen title — is identified by id watermark and removed on
    the way out, pass or fail, so nothing leaks into the shared session-scoped
    database.
    """
    with Session(db_engine) as probe:
        watermark = probe.scalar(select(Submission.id).order_by(Submission.id.desc())) or 0

    try:
        yield _api_test_user, watermark
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(
                delete(SubmissionAuditEvent).where(
                    SubmissionAuditEvent.submission_id > watermark
                )
            )
            cleanup.execute(
                delete(Submission).where(Submission.id > watermark)
            )
            cleanup.execute(
                delete(Species).where(Species.smiles.like(f"[He]{MARKER}%"))
            )
            cleanup.commit()


def _open(session: Session, user_id: int, suffix: str):
    return open_upload_submission(
        session,
        created_by=user_id,
        kind=SubmissionKind.conformer,
        title=f"{MARKER}-{suffix}",
    )


class TestIngestionAuditCannotDestroyTheUpload:
    def test_payload_survives_an_unwritable_ingestion_event(
        self, db_engine, committed_scratch
    ) -> None:
        """The upload commits even though its audit event cannot be stored."""
        user_id, watermark = committed_scratch

        with Session(db_engine) as session:
            sub = _open(session, user_id, "survives")
            session.add(Species(**_species_kwargs("-a")))

            assert mark_upload_ingested(session, sub, summary=UNSTORABLE) is False

            submission_id = sub.submission_id
            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-a")
            ) is not None, (
                "the upload was destroyed by an audit event describing it"
            )
            kinds = set(
                verify.scalars(
                    select(SubmissionAuditEvent.event_kind).where(
                        SubmissionAuditEvent.submission_id == submission_id
                    )
                ).all()
            )
            # The event that failed is missing; the rest of the trail is not.
            assert SubmissionAuditEventKind.ingestion_succeeded not in kinds
            assert SubmissionAuditEventKind.submission_created in kinds
            assert verify.get(Submission, submission_id) is not None

    def test_unflushed_payload_is_not_swept_into_the_savepoint(
        self, db_engine, committed_scratch
    ) -> None:
        """Rows still pending when the audit event runs must survive it.

        ``mark_upload_ingested`` flushes before opening the savepoint. Without
        that, a workflow's not-yet-flushed rows would be INSERTed *inside* the
        savepoint and rolled back with the failing event.
        """
        user_id, watermark = committed_scratch

        with Session(db_engine) as session:
            sub = _open(session, user_id, "unflushed")
            # Deliberately never flushed by the test.
            session.add(Species(**_species_kwargs("-b")))

            assert mark_upload_ingested(session, sub, summary=UNSTORABLE) is False
            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-b")
            ) is not None

    def test_session_stays_usable_for_the_rest_of_the_request(
        self, db_engine, committed_scratch
    ) -> None:
        """``idem.record`` runs on the next line and must still be able to write."""
        user_id, watermark = committed_scratch

        with Session(db_engine) as session:
            sub = _open(session, user_id, "usable")
            session.add(Species(**_species_kwargs("-c")))
            mark_upload_ingested(session, sub, summary=UNSTORABLE)

            # A poisoned transaction would raise PendingRollbackError here.
            session.add(Species(**_species_kwargs("-d")))
            session.commit()

        with Session(db_engine) as verify:
            found = {
                s.smiles
                for s in verify.scalars(
                    select(Species).where(Species.smiles.like(f"[He]{MARKER}-%"))
                ).all()
            }
            assert {f"[He]{MARKER}-c", f"[He]{MARKER}-d"} <= found

    def test_failure_is_logged_loudly(
        self, db_engine, committed_scratch, caplog
    ) -> None:
        """A silently-skipped audit event is how the incident stayed invisible."""
        user_id, watermark = committed_scratch

        with caplog.at_level(
            logging.ERROR, logger="app.services.upload_submission"
        ):
            with Session(db_engine) as session:
                sub = _open(session, user_id, "logged")
                mark_upload_ingested(session, sub, summary=UNSTORABLE)
                session.rollback()

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors
        assert "ingestion_succeeded" in " ".join(r.getMessage() for r in errors)

    def test_success_path_is_unchanged(
        self, db_engine, committed_scratch
    ) -> None:
        """A storable summary still commits atomically with the payload."""
        user_id, watermark = committed_scratch

        with Session(db_engine) as session:
            sub = _open(session, user_id, "normal")
            session.add(Species(**_species_kwargs("-e")))
            assert mark_upload_ingested(session, sub) is True
            submission_id = sub.submission_id
            session.commit()

        with Session(db_engine) as verify:
            kinds = set(
                verify.scalars(
                    select(SubmissionAuditEvent.event_kind).where(
                        SubmissionAuditEvent.submission_id == submission_id
                    )
                ).all()
            )
            assert SubmissionAuditEventKind.ingestion_succeeded in kinds
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-e")
            ) is not None

    def test_a_rolled_back_upload_still_discards_the_event(
        self, db_engine, committed_scratch
    ) -> None:
        """Releasing the savepoint does not make the event durable on its own."""
        user_id, watermark = committed_scratch

        with Session(db_engine) as session:
            sub = _open(session, user_id, "rollback")
            session.add(Species(**_species_kwargs("-f")))
            mark_upload_ingested(session, sub)
            submission_id = sub.submission_id
            session.rollback()

        with Session(db_engine) as verify:
            assert verify.get(Submission, submission_id) is None
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-f")
            ) is None


class TestCommitTimeFailureIsAudited:
    """The blind spot: ``get_write_db`` commits after the route has returned.

    ``audit_sync_upload_failure`` wraps the route function, so a failure
    raised by the commit in dependency teardown lands outside its ``try``.
    Before this, the failure class the 2026-08-05 incident belongs to left no
    audit row at all.
    """

    def _failed_submissions(
        self, session: Session, user_id: int, watermark: int
    ) -> list[Submission]:
        """Failed submissions this test created.

        ``record_failed_upload`` picks its own title, so these are identified
        by the fixture's id watermark rather than by marker.
        """
        return list(
            session.scalars(
                select(Submission)
                .where(
                    Submission.created_by == user_id,
                    Submission.status == SubmissionStatus.failed,
                    Submission.id > watermark,
                )
                .order_by(Submission.id)
            ).all()
        )

    @pytest.fixture(autouse=True)
    def _bind_app_sessions_to_the_test_database(self, db_engine, monkeypatch):
        """Point the app's own session factory at the per-run test database.

        Both ``get_write_db`` and ``record_failed_upload`` open sessions from
        ``app.api.deps.SessionLocal``, which is bound to the configured
        deployment engine at import time. These tests drive the real
        dependency rather than a fixture stand-in, so the factory itself has
        to be redirected.
        """
        monkeypatch.setattr(
            api_deps, "SessionLocal", lambda: Session(bind=db_engine)
        )

    def test_a_failure_raised_by_the_commit_is_audited(
        self, db_engine, committed_scratch
    ) -> None:
        """Drive ``get_write_db`` exactly as FastAPI does, and fail at COMMIT."""
        user_id, watermark = committed_scratch

        before = None
        with Session(db_engine) as verify:
            before = len(self._failed_submissions(verify, user_id, watermark))

        generator = get_write_db()
        session = next(generator)
        try:
            # What the decorator parks before calling the route body.
            session.info[SYNC_UPLOAD_AUDIT_KEY] = {
                "created_by": user_id,
                "kind": SubmissionKind.conformer,
                "audited": False,
            }
            # Never flushed, so this INSERT is issued by COMMIT itself —
            # after the route returned, which is the whole point.
            session.add(
                Species(
                    **{
                        **_species_kwargs("-commitfail"),
                        "smiles": f"[He]{MARKER}" + chr(0) + "x",
                    }
                )
            )
            with pytest.raises(Exception):
                # Resuming the generator runs the commit and its error path,
                # exactly as FastAPI's exit stack does at teardown.
                next(generator)
        finally:
            generator.close()

        with Session(db_engine) as verify:
            after = self._failed_submissions(verify, user_id, watermark)
            assert len(after) == before + 1, (
                "a commit-time upload failure left no durable audit row"
            )
            reasons = list(
                verify.scalars(
                    select(SubmissionAuditEvent.reason).where(
                        SubmissionAuditEvent.submission_id == after[-1].id,
                        SubmissionAuditEvent.event_kind
                        == SubmissionAuditEventKind.ingestion_failed,
                    )
                ).all()
            )
            assert reasons and reasons[0]

    def test_no_audit_row_for_sessions_that_are_not_uploads(
        self, db_engine, committed_scratch
    ) -> None:
        """Every other write route shares ``get_write_db``; none may be affected."""
        user_id, watermark = committed_scratch

        with Session(db_engine) as verify:
            before = len(self._failed_submissions(verify, user_id, watermark))

        generator = get_write_db()
        session = next(generator)
        try:
            session.add(
                Species(
                    **{
                        **_species_kwargs("-plain"),
                        "smiles": f"[He]{MARKER}" + chr(0) + "y",
                    }
                )
            )
            with pytest.raises(Exception):
                next(generator)
        finally:
            generator.close()

        with Session(db_engine) as verify:
            assert len(self._failed_submissions(verify, user_id, watermark)) == before

    def test_a_failure_the_decorator_already_audited_is_not_audited_twice(
        self, db_engine, committed_scratch
    ) -> None:
        """The decorator records first and marks the intent spent."""
        user_id, watermark = committed_scratch

        with Session(db_engine) as verify:
            before = len(self._failed_submissions(verify, user_id, watermark))

        with Session(db_engine) as session:
            session.info[SYNC_UPLOAD_AUDIT_KEY] = {
                "created_by": user_id,
                "kind": SubmissionKind.conformer,
                "audited": True,
            }
            audit_upload_failure_at_commit(session, RuntimeError("already handled"))

        with Session(db_engine) as verify:
            assert len(self._failed_submissions(verify, user_id, watermark)) == before


class TestBundleImportAuditCannotDestroyTheImport:
    """The same shape at the largest payload the API accepts.

    ``submit_contribution_bundle`` appends ``ingestion_succeeded`` after
    importing a whole bundle: the lowest failure probability in the flow,
    attached to the biggest blast radius.
    """

    def test_the_imported_bundle_survives_an_unwritable_audit_event(
        self, db_engine, committed_scratch, monkeypatch
    ) -> None:
        from app.schemas.workflows.contribution_bundle import BundleKind
        from app.workflows import contribution_bundle_submit as bundle_submit

        def _refuse(session_, **kwargs):
            session_.execute(text("SELECT 1 FROM table_that_does_not_exist"))

        monkeypatch.setattr(bundle_submit, "mark_ingestion_succeeded", _refuse)

        user_id, watermark = committed_scratch

        with Session(db_engine) as session:
            sub = _open(session, user_id, "bundle")
            session.add(Species(**_species_kwargs("-bundle")))

            assert bundle_submit._append_import_audit(
                session,
                submission=sub.submission,
                bundle_kind=BundleKind.thermo,
                imported_count=3,
                linked_count=1,
            ) is False

            submission_id = sub.submission_id
            session.commit()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-bundle")
            ) is not None, (
                "the imported bundle was destroyed by its own audit event"
            )
            kinds = set(
                verify.scalars(
                    select(SubmissionAuditEvent.event_kind).where(
                        SubmissionAuditEvent.submission_id == submission_id
                    )
                ).all()
            )
            assert SubmissionAuditEventKind.ingestion_succeeded not in kinds
            assert SubmissionAuditEventKind.submission_created in kinds
