"""Every selection writer locks the record it names before appending a row.

An admin deletion locks the record, checks that nothing selects it, then deletes. A selection appended in between would
name a deleted record, so each writer takes the same row lock first. The lock itself is exercised against a second
connection in ``tests/db/test_record_deletion_committed.py``.
"""

from __future__ import annotations

from app.db.models.common import SubmissionRecordType
from app.services.release import curation
from app.services.release.curation import add_selection, supersede_selection, withdraw_selection


def test_add_supersede_and_withdraw_each_lock_the_named_record(
    db_session, monkeypatch, draft_release, curator, thermo_candidates, species_entry
) -> None:
    first, second = thermo_candidates
    locked: list[tuple[SubmissionRecordType, int]] = []
    real = curation.lock_record_for_selection

    def spy(session, *, record_type, record_id):
        locked.append((record_type, record_id))
        return real(session, record_type=record_type, record_id=record_id)

    monkeypatch.setattr(curation, "lock_record_for_selection", spy)

    original = add_selection(
        db_session,
        release=draft_release,
        record_type=SubmissionRecordType.thermo,
        record_id=first.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="first choice",
        selected_by=curator.id,
    )
    replacement = supersede_selection(
        db_session, superseded=original, record_id=second.id, rationale="better", selected_by=curator.id
    )
    withdraw_selection(db_session, superseded=replacement, rationale="neither", selected_by=curator.id)

    thermo = SubmissionRecordType.thermo
    assert locked == [(thermo, first.id), (thermo, second.id), (thermo, second.id)]
