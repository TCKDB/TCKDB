"""Release artifact line order does not depend on public refs.

Public refs are random per database, so ordering lines by them makes the same
release render differently in two databases built the same way. The lines follow
integer ids, which an identical build sequence reproduces.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.services.record_review import set_record_review_status
from app.services.release.artifacts import render_artifacts
from app.services.release.curation import add_selection
from app.services.submission import link_records
from tests.services.scientific_read._factories import make_species, make_species_entry, make_thermo_scalar


def _lines(artifacts, path: str) -> list[dict]:
    artifact = next(a for a in artifacts if a.path == path)
    return [json.loads(line) for line in artifact.content.decode().splitlines() if line]


def _set_refs(session, table: str, refs_by_id: dict[int, str]) -> None:
    session.execute(text("SET LOCAL session_replication_role = replica"))
    for row_id, ref in refs_by_id.items():
        session.execute(text(f"UPDATE {table} SET public_ref = :ref WHERE id = :id"), {"ref": ref, "id": row_id})
    session.execute(text("SET LOCAL session_replication_role = origin"))
    session.expire_all()


def test_release_lines_follow_ids_not_public_refs(db_session, draft_release, curator, attested_submission):
    records = []
    for smiles, h298 in (("CCO", -234.5), ("CC", -84.0)):
        entry = make_species_entry(db_session, species=make_species(db_session, smiles=smiles))
        thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=h298, s298_j_mol_k=280.0)
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=thermo.id,
            status=RecordReviewStatus.approved,
            actor=curator,
            note="approved",
        )
        records.append((entry, thermo))
    link_records(
        db_session,
        submission=attested_submission,
        records=[(SubmissionRecordType.thermo, thermo.id, None) for _, thermo in records],
    )
    selections = [
        add_selection(
            db_session,
            release=draft_release,
            record_type=SubmissionRecordType.thermo,
            record_id=thermo.id,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=entry.id,
            rationale="chosen",
            selected_by=curator.id,
        )
        for entry, thermo in records
    ]
    thermo_ids = [thermo.id for _, thermo in records]
    selection_ids = [s.id for s in selections]
    assert thermo_ids == sorted(thermo_ids) and selection_ids == sorted(selection_ids)

    # Refs that sort the opposite way to the ids.
    _set_refs(
        db_session,
        "thermo",
        {thermo_ids[0]: "th_zzzzzzzzzzzzzzzzzzzzzzzzzz", thermo_ids[1]: "th_aaaaaaaaaaaaaaaaaaaaaaaaaa"},
    )
    _set_refs(
        db_session,
        "release_selection",
        {selection_ids[0]: "rsel_zzzzzzzzzzzzzzzzzzzzzzzzzz", selection_ids[1]: "rsel_aaaaaaaaaaaaaaaaaaaaaaaaaa"},
    )

    artifacts = render_artifacts(db_session, draft_release)
    expected = ["th_zzzzzzzzzzzzzzzzzzzzzzzzzz", "th_aaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert [line["record_ref"] for line in _lines(artifacts, "selected_records.ndjson")] == expected
    assert [line["record_ref"] for line in _lines(artifacts, "candidate_records.ndjson")][:2] == expected
    assert [line["record_ref"] for line in _lines(artifacts, "review_history.ndjson")][:2] == expected
