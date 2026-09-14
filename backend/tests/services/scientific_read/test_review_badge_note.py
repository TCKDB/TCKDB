"""The curator's stated reason must reach a reader, not just the status.

A record projected as ``under_review`` with no reason is a warning nobody
can act on. These tests pin that ``RecordReview.note`` is carried into the
public projection, and that it is carried for EVERY record type rather than
only the one it was first needed for -- every per-record loader delegates to
``fetch_review_badges``, so the coverage is structural rather than a list
somebody has to remember to extend.
"""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.services.scientific_read.common import fetch_review_badges

from ._factories import make_species, make_species_entry, make_thermo_scalar, set_review

NOTE = (
    "Barriers are sound; bond-fission asymptotes are systematically low. "
    "N2H4 -> 2 NH2 is 27.4 kJ/mol below the Active Thermochemical Tables value."
)


@pytest.mark.parametrize(
    "record_type",
    [
        SubmissionRecordType.network_solve,
        SubmissionRecordType.calculation,
        SubmissionRecordType.reaction_entry,
        SubmissionRecordType.species_entry,
        SubmissionRecordType.thermo,
    ],
)
def test_note_is_projected_for_every_record_type(db_session, record_type):
    db_session.add(
        RecordReview(
            record_type=record_type,
            record_id=4242,
            status=RecordReviewStatus.under_review,
            note=NOTE,
        )
    )
    db_session.flush()

    badges = fetch_review_badges(
        db_session, record_type=record_type, record_ids=[4242]
    )

    assert badges[4242].status is RecordReviewStatus.under_review
    assert badges[4242].note == NOTE, (
        "the curator's reason was dropped from the public projection, so a "
        "reader sees a warning with no explanation"
    )


def test_a_record_with_no_review_row_has_no_note(db_session):
    badges = fetch_review_badges(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_ids=[999_999],
    )
    assert badges[999_999].status is RecordReviewStatus.not_reviewed
    assert badges[999_999].note is None


def test_a_review_row_without_a_note_projects_none_not_empty_string(db_session):
    # An absent reason must read as absent, never as a blank reason -- the
    # same absence-vs-zero rule the rest of this surface follows.
    #
    # Status is ``under_review`` (not ``approved``) deliberately: an
    # ``approved`` row makes ``tckdb_guard_record_review`` lock the
    # underlying scientific record via ``tckdb_lock_scientific_record``,
    # which requires that record to actually exist. This test is about the
    # note, not the approval path, so it uses the same fake-record-id shape
    # as the parametrized test above.
    db_session.add(
        RecordReview(
            record_type=SubmissionRecordType.thermo,
            record_id=7171,
            status=RecordReviewStatus.under_review,
            note=None,
        )
    )
    db_session.flush()

    badges = fetch_review_badges(
        db_session, record_type=SubmissionRecordType.thermo, record_ids=[7171]
    )
    assert badges[7171].note is None


def test_note_is_not_gated_on_approved_status(db_session):
    """A note on an ``approved`` record is as meaningful as one under review.

    Uses a real thermo record (rather than a fake id) because approving a
    review row locks the underlying scientific record via
    ``tckdb_lock_scientific_record``, which requires it to exist.
    """
    species = make_species(db_session)
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)

    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        status=RecordReviewStatus.approved,
        note=NOTE,
    )

    badges = fetch_review_badges(
        db_session, record_type=SubmissionRecordType.thermo, record_ids=[thermo.id]
    )

    assert badges[thermo.id].status is RecordReviewStatus.approved
    assert badges[thermo.id].note == NOTE, (
        "the note must reach the reader on an approved record too -- it is "
        "not gated on review status"
    )
