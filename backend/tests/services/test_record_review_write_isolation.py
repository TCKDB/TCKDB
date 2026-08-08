"""Two contributors depositing against one shared identity must both survive.

``ensure_record_review`` is check-then-insert against
``uq_record_review_record``. Its targets include *reused identity rows* —
``species_entry``, ``reaction_entry``, ``conformer_group`` — that resolution
deliberately dedupes across uploads. So two contributors depositing against
the same molecule concurrently both read ``None`` inside
``ensure_record_review``, both INSERT, and on the pre-fix code the loser takes
an ``IntegrityError`` that rolls back its entire upload: hundreds of science
rows destroyed by a bookkeeping row describing something the winner had
already recorded.

No exotic content is required to trigger this — just two people working at
once on a shared species, which is the normal case for a shared database.

The window is *inside* ``ensure_record_review``, between its own ``SELECT``
and its ``INSERT``; a caller that looks first does not widen it, because the
helper looks again under ``READ COMMITTED`` and sees the winner's committed
row. Two tests below enter that window from both directions: one
deterministically (the winner commits between the loser's SELECT and its
INSERT) and one for real (the loser's INSERT blocks on the winner's
uncommitted index entry, which is the path two simultaneous uploads actually
take).

The load-bearing assertion in every test is made from a **fresh session after
a genuine top-level commit**, never merely "no exception propagated".

``ensure_record_review`` is documented as idempotent ("First call inserts.
Subsequent calls return the existing row unchanged."). These tests pin that
the promise now holds across transactions as well as within one.
"""

from __future__ import annotations

import itertools
import threading
import time

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.common import (
    MoleculeKind,
    RecordReviewStatus,
    StereoKind,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview, RecordReviewEvent
from app.db.models.species import Species
from app.services import record_review as record_review_service
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
    ensure_record_review,
    get_record_review,
)

MARKER = "revrace"

#: Synthetic ids, allocated once per module so committed rows from one test
#: can never be mistaken for a fresh identity by the next. ``record_review``
#: is polymorphic over ``record_type`` and carries no FK on ``record_id``, so
#: these need not resolve to live rows for the uniqueness race to be exactly
#: the real one. ``record_review_event`` is append-only at the database level
#: (``tckdb_reject_mutation``), so these rows are deliberately never deleted —
#: the per-run test database is dropped wholesale instead.
_record_ids = itertools.count(900_100)


def _species_kwargs(suffix: str) -> dict:
    """A minimal but real science row — the 'payload' these tests protect."""
    return {
        "kind": MoleculeKind.molecule,
        "smiles": f"[He]{MARKER}{suffix}",
        "inchi_key": "SWQJXJOGLNCZEY-UHFFFAOYSA-N",
        "charge": 0,
        "multiplicity": 1,
        "stereo_kind": StereoKind.unspecified,
    }


@pytest.fixture
def committed_scratch(db_engine):
    """A committing scratch space with guaranteed payload cleanup.

    Every other fixture in the suite isolates by rolling a transaction back,
    which is precisely the thing under test here: the loser's rollback is the
    bug. So these tests commit for real.

    Yields a fresh ``record_id`` allocator. Payload species rows are deleted
    on the way out, whether the test passed or failed.
    """
    try:
        yield lambda: next(_record_ids)
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(
                delete(Species).where(Species.smiles.like(f"[He]{MARKER}%"))
            )
            cleanup.commit()


def _commit_winner_during(monkeypatch, *, winner: Session, ref: RecordRef):
    """Make the winner commit inside the loser's check-then-insert window.

    ``ensure_record_review`` reads, finds nothing, and inserts. This patches
    the read so that the *first* time it returns ``None``, the competing
    transaction commits its own row for the same identity before the caller
    proceeds to INSERT — the exact interleaving of two uploads resolving to
    one deduped ``species_entry``, made deterministic.
    """
    real_get = record_review_service.get_record_review
    fired = {"done": False}

    def _racing_get(session, *, record_type, record_id):
        found = real_get(session, record_type=record_type, record_id=record_id)
        if (
            found is None
            and not fired["done"]
            and record_type is ref.record_type
            and record_id == ref.record_id
        ):
            fired["done"] = True
            real_ensure = record_review_service.ensure_record_review
            monkeypatch.setattr(
                record_review_service, "get_record_review", real_get
            )
            try:
                real_ensure(
                    winner,
                    record_type=ref.record_type,
                    record_id=ref.record_id,
                    status=RecordReviewStatus.under_review,
                )
                winner.commit()
            finally:
                monkeypatch.setattr(
                    record_review_service, "get_record_review", _racing_get
                )
        return found

    monkeypatch.setattr(record_review_service, "get_record_review", _racing_get)
    return fired


class TestConcurrentDepositAgainstOneIdentity:
    """The live race: two uploads, one shared species_entry, no exotic content."""

    def test_loser_adopts_the_existing_review_row_and_keeps_its_payload(
        self, db_engine, committed_scratch, monkeypatch
    ) -> None:
        """The winner commits inside the loser's check-then-insert window.

        On the pre-fix code the loser's INSERT violates
        ``uq_record_review_record``, the ``IntegrityError`` poisons its
        transaction, ``get_write_db`` rolls it back, and every scientific row
        the loser had written goes with it — while nothing was wrong with the
        loser's science and nothing was wrong with the review row either.
        """
        record_id = committed_scratch()
        ref = RecordRef(SubmissionRecordType.species_entry, record_id)

        winner = Session(db_engine)
        loser = Session(db_engine)
        try:
            winner.add(Species(**_species_kwargs("-winner")))
            # The loser has already written its science by the time it gets
            # here — the review row is the *last* statement of every upload
            # workflow.
            loser.add(Species(**_species_kwargs("-loser")))

            fired = _commit_winner_during(monkeypatch, winner=winner, ref=ref)

            review = ensure_record_review(
                loser,
                record_type=ref.record_type,
                record_id=ref.record_id,
                status=RecordReviewStatus.under_review,
            )
            assert fired["done"], "the race window was never entered"
            adopted_review_id = review.id

            loser.commit()
        finally:
            winner.close()
            loser.close()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-loser")
            ) is not None, (
                "the losing contributor's upload was destroyed by a review row "
                "describing an identity the winner had already recorded"
            )
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-winner")
            ) is not None
            rows = verify.scalars(
                select(RecordReview).where(RecordReview.record_id == record_id)
            ).all()
            assert len(rows) == 1, (
                "the shared identity must carry exactly one review row"
            )
            assert adopted_review_id == rows[0].id, (
                "the loser must adopt the row the winner created for this "
                "shared identity, not invent a second one"
            )

    def test_loser_blocked_on_the_index_also_adopts(
        self, db_engine, committed_scratch
    ) -> None:
        """The genuinely-concurrent path: the loser's INSERT waits on the index.

        This is the wide window, and the one two simultaneous uploads actually
        take. The winner INSERTs its review row and then holds the uncommitted
        unique-index entry for the remainder of its transaction — the rest of
        an upload, potentially seconds. Any contributor whose ``SELECT`` lands
        anywhere in that period sees ``None``, blocks on the index at INSERT,
        and takes the violation the moment the winner commits.

        The handoff below is fully ordered, so this reproduces every run
        rather than depending on thread scheduling.
        """
        record_id = committed_scratch()
        ref = RecordRef(SubmissionRecordType.species_entry, record_id)

        loser_selected = threading.Event()
        winner_inserted = threading.Event()
        outcome: dict[str, object] = {}

        def _loser() -> None:
            with Session(db_engine) as session:
                try:
                    assert get_record_review(
                        session,
                        record_type=ref.record_type,
                        record_id=ref.record_id,
                    ) is None
                    session.add(Species(**_species_kwargs("-blocked")))
                    session.flush()
                    loser_selected.set()
                    assert winner_inserted.wait(timeout=30)
                    # The winner now holds the uncommitted index entry; this
                    # INSERT blocks until the winner commits, then violates.
                    review = ensure_record_review(
                        session,
                        record_type=ref.record_type,
                        record_id=ref.record_id,
                        status=RecordReviewStatus.under_review,
                    )
                    outcome["review_id"] = review.id
                    session.commit()
                except BaseException as exc:  # surfaced in the main thread
                    outcome["error"] = exc
                    session.rollback()

        with Session(db_engine) as winner:
            assert get_record_review(
                winner, record_type=ref.record_type, record_id=ref.record_id
            ) is None

            thread = threading.Thread(target=_loser, daemon=True)
            thread.start()
            assert loser_selected.wait(timeout=30)

            review_winner = ensure_record_review(
                winner,
                record_type=ref.record_type,
                record_id=ref.record_id,
                status=RecordReviewStatus.under_review,
            )
            winner_review_id = review_winner.id
            winner_inserted.set()
            # Let the loser actually reach (and block on) its INSERT, so the
            # lock-wait path is the one exercised rather than a plain re-read.
            time.sleep(1.5)
            winner.commit()

        thread.join(timeout=30)
        assert not thread.is_alive()
        assert "error" not in outcome, (
            f"the blocked contributor's upload failed: {outcome.get('error')!r}"
        )
        assert outcome["review_id"] == winner_review_id

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-blocked")
            ) is not None
            rows = verify.scalars(
                select(RecordReview).where(RecordReview.record_id == record_id)
            ).all()
            assert len(rows) == 1

    def test_adopted_row_emits_no_duplicate_created_event(
        self, db_engine, committed_scratch, monkeypatch
    ) -> None:
        """Adoption must not append a second ``created`` event.

        ``record_review_event`` is append-only history, enforced by a database
        trigger. The row was created once; a loser that re-stamps ``created``
        would make the history claim the identity entered review twice.
        """
        record_id = committed_scratch()
        ref = RecordRef(SubmissionRecordType.species_entry, record_id)

        winner = Session(db_engine)
        loser = Session(db_engine)
        try:
            _commit_winner_during(monkeypatch, winner=winner, ref=ref)
            ensure_record_review(
                loser,
                record_type=ref.record_type,
                record_id=ref.record_id,
                status=RecordReviewStatus.under_review,
            )
            loser.commit()
        finally:
            winner.close()
            loser.close()

        with Session(db_engine) as verify:
            review = verify.scalar(
                select(RecordReview).where(RecordReview.record_id == record_id)
            )
            events = verify.scalars(
                select(RecordReviewEvent).where(
                    RecordReviewEvent.record_review_id == review.id
                )
            ).all()
            assert len(events) == 1

    def test_apply_review_policy_survives_the_race_end_to_end(
        self, db_engine, committed_scratch, monkeypatch
    ) -> None:
        """The workflow-level entry point, which is what all 11 uploads call."""
        record_id = committed_scratch()
        ref = RecordRef(SubmissionRecordType.species_entry, record_id)
        policy = ReviewPolicy(status=RecordReviewStatus.under_review)

        winner = Session(db_engine)
        loser = Session(db_engine)
        try:
            loser.add(Species(**_species_kwargs("-policy")))
            _commit_winner_during(monkeypatch, winner=winner, ref=ref)

            reviews = apply_review_policy(
                loser, targets=[ref], policy=policy, created_by=None
            )
            assert len(reviews) == 1
            loser.commit()
        finally:
            winner.close()
            loser.close()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-policy")
            ) is not None


class TestReviewRowStaysCoupledToTheUpload:
    """Isolation is *not* blanket-applied here, and that is deliberate.

    A ``record_review`` row is not a description of the science — it is the
    record's admission ticket into the review pipeline. Without it a deposited
    record reads as ``not_reviewed`` (see ``_NO_REVIEW_ROW_RANK`` in
    ``app.services.scientific_read.sql_review``), indistinguishable from a
    legacy internal record, and no curator can ever approve it. Swallowing a
    review-row failure would store stranded science and return ``201``.

    Unlike the idempotency receipt, this runs *before* the response is
    determined, so failing here yields an honest ``5xx`` and a client that
    knows to retry. The race is the bug; the coupling is correct.
    """

    def test_a_terminal_status_request_still_aborts_the_upload(
        self, db_engine, committed_scratch
    ) -> None:
        """A terminal target status is a caller bug and must not be swallowed."""
        from app.api.errors import DomainError

        record_id = committed_scratch()

        with Session(db_engine) as session:
            session.add(Species(**_species_kwargs("-aborts")))
            with pytest.raises(DomainError):
                ensure_record_review(
                    session,
                    record_type=SubmissionRecordType.species_entry,
                    record_id=record_id,
                    status=RecordReviewStatus.approved,
                )
            session.rollback()

        with Session(db_engine) as verify:
            assert verify.scalar(
                select(Species).where(Species.smiles == f"[He]{MARKER}-aborts")
            ) is None


class TestUnrelatedIntegrityErrorsStillPropagate:
    """Adoption is scoped to the identity collision, nothing else."""

    def test_a_foreign_key_violation_is_not_swallowed(
        self, db_engine, committed_scratch
    ) -> None:
        """``submission_id`` pointing nowhere must still fail loudly.

        A blanket ``except IntegrityError: re-select`` would find nothing on
        the re-read and would have to invent an answer, masking a genuinely
        broken write.
        """
        record_id = committed_scratch()

        with Session(db_engine) as session:
            with pytest.raises(IntegrityError):
                ensure_record_review(
                    session,
                    record_type=SubmissionRecordType.species_entry,
                    record_id=record_id,
                    status=RecordReviewStatus.under_review,
                    submission_id=2_000_000_001,
                )
            session.rollback()
