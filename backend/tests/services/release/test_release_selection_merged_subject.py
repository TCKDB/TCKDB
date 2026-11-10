"""A release selection about a species entry that was merged away still counts for the survivor (DR-0038).

Selections are append-only and stay on the row they were made about. After the stereo-identity migration the thermo they
chose has moved to the surviving entry; so a selection is read as a selection for the survivor, never rewritten:

* ``current_selection(survivor)`` returns it;
* it can be superseded, and the new row names the survivor, whose thermo the replacement must belong to;
* a second selection for the same survivor is refused as already standing;
* the release manifest renders the subject as the survivor.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.db.models.common import SubmissionRecordType
from app.services.release.artifacts import render_artifacts
from app.services.release.curation import (
    ReleaseCurationError,
    ReleaseStateConflict,
    add_selection,
    current_selection,
    supersede_selection,
)
from tests.services.scientific_read._factories import make_species_entry

_REVISION = "test-selection-forwarding"


def _declare_and_move_thermo(session, thermo_ids, survivor_id: int) -> None:
    """What the migration does: a declared repair re-points the approved thermo at the surviving entry."""
    session.execute(
        text(
            "INSERT INTO accepted_science_repair (target_table, declared_columns, alembic_revision, reason) "
            "VALUES ('thermo', CAST('{species_entry_id}' AS text[]), :rev, 'test: move to the survivor')"
        ),
        {"rev": _REVISION},
    )
    for thermo_id in thermo_ids:
        session.execute(
            text("UPDATE thermo SET species_entry_id = :s WHERE id = :t"), {"s": survivor_id, "t": thermo_id}
        )


@pytest.fixture
def merged_subject(db_session, draft_release, curator, thermo_candidates, species_entry):
    """The first candidate is selected for ``species_entry``; then ``species_entry`` is merged into a survivor."""
    first, second = thermo_candidates
    selection = add_selection(
        db_session,
        release=draft_release,
        record_type=SubmissionRecordType.thermo,
        record_id=first.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="chosen before the merge",
        selected_by=curator.id,
    )
    survivor = make_species_entry(db_session, species_entry.species, stereo_label="Z")
    _declare_and_move_thermo(db_session, [first.id, second.id], survivor.id)
    db_session.execute(
        text("UPDATE species_entry SET merged_into_species_entry_id = :s WHERE id = :l"),
        {"s": survivor.id, "l": species_entry.id},
    )
    db_session.expire_all()
    return selection, survivor, species_entry, first, second


def _selection_for(db_session, release, subject_id, record_type=SubmissionRecordType.thermo):
    return current_selection(
        db_session,
        release=release,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=subject_id,
        record_type=record_type,
    )


def test_the_moved_selection_is_the_survivors_current_selection(db_session, draft_release, merged_subject) -> None:
    selection, survivor, kept, _first, _second = merged_subject
    assert _selection_for(db_session, draft_release, survivor.id).id == selection.id
    assert _selection_for(db_session, draft_release, kept.id).id == selection.id
    # The append-only row itself was not rewritten.
    assert selection.subject_id == kept.id


def test_a_selection_about_a_merged_entry_can_be_superseded(
    db_session, draft_release, second_curator, merged_subject
) -> None:
    selection, survivor, kept, _first, second = merged_subject
    replacement = supersede_selection(
        db_session,
        superseded=selection,
        record_id=second.id,
        rationale="the other candidate is closer",
        selected_by=second_curator.id,
    )
    assert replacement.subject_id == survivor.id, "the new decision is about the row that holds the results"
    assert _selection_for(db_session, draft_release, survivor.id).id == replacement.id
    assert _selection_for(db_session, draft_release, kept.id).id == replacement.id


def test_a_second_selection_for_the_survivor_is_refused_as_already_standing(
    db_session, draft_release, curator, merged_subject
) -> None:
    _selection, survivor, _kept, _first, second = merged_subject
    with pytest.raises(ReleaseStateConflict, match="selection_already_stands"):
        add_selection(
            db_session,
            release=draft_release,
            record_type=SubmissionRecordType.thermo,
            record_id=second.id,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=survivor.id,
            rationale="a duplicate",
            selected_by=curator.id,
        )


def test_the_chosen_record_must_belong_to_the_survivor_not_to_a_third_entry(
    db_session, draft_release, curator, merged_subject, species_entry
) -> None:
    _selection, survivor, _kept, _first, second = merged_subject
    other = make_species_entry(db_session, species_entry.species, stereo_label="E")
    with pytest.raises(ReleaseCurationError, match="record_subject_mismatch"):
        add_selection(
            db_session,
            release=draft_release,
            record_type=SubmissionRecordType.thermo,
            record_id=second.id,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=other.id,
            rationale="wrong subject",
            selected_by=curator.id,
        )


def test_the_manifest_renders_the_subject_as_the_survivor(db_session, draft_release, merged_subject) -> None:
    selection, survivor, kept, first, _second = merged_subject
    artifacts = {a.path: a for a in render_artifacts(db_session, draft_release)}
    selected = [json.loads(line) for line in artifacts["selected_records.ndjson"].content.decode().splitlines() if line]
    assert [row["subject_ref"] for row in selected] == [survivor.public_ref]
    assert kept.public_ref not in artifacts["selected_records.ndjson"].content.decode()
    assert first.public_ref in artifacts["selected_records.ndjson"].content.decode()
