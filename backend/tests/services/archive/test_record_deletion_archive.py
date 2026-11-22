"""The deletion audit travels with an archive, and a restored audit row never opens the guards.

``record_deletion`` is the permanent account of what an admin removed, so a restore that dropped it would turn every
deleted ref from 410 into 404 and lose the copy of the removed rows. Its ``xact_id`` is a transaction id of the source
cluster and is not carried: the restored rows hold NULL, which the permit can never equal.
"""

from __future__ import annotations

import io

from sqlalchemy import select

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.record_deletion import RecordDeletion
from app.services.archive import restore_archive, write_archive
from app.services.record_deletion import delete_approved_record, deleted_records
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.archive.test_archive import _empty_archive_tables
from tests.services.scientific_read._factories import make_calculation, make_species, make_species_entry, next_inchi_key


def _deleted_calculation(session) -> tuple[str, dict]:
    admin = AppUser(username="archive-deletion-admin", role=AppUserRole.admin)
    curator = AppUser(username="archive-deletion-curator", role=AppUserRole.curator)
    session.add_all([admin, curator])
    session.flush()
    entry = make_species_entry(session, make_species(session, inchi_key=next_inchi_key("ARCDEL")))
    calculation = make_calculation(session, species_entry_id=entry.id)
    ensure_record_review(session, record_type=SubmissionRecordType.calculation, record_id=calculation.id)
    set_record_review_status(
        session,
        record_type=SubmissionRecordType.calculation,
        record_id=calculation.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )
    ref = calculation.public_ref
    deletion = delete_approved_record(
        session, record_type=SubmissionRecordType.calculation, public_ref=ref, reason="duplicate deposit", actor=admin
    )
    return ref, deletion.snapshot


def test_the_audit_survives_an_archive_round_trip_without_its_transaction_id(client, db_session) -> None:
    ref, snapshot = _deleted_calculation(db_session)
    archive = io.BytesIO()
    write_archive(db_session, archive)
    _empty_archive_tables(db_session)
    assert db_session.scalar(select(RecordDeletion)) is None

    report = restore_archive(db_session, io.BytesIO(archive.getvalue()))

    assert report.table_row_counts["record_deletion"] == 1
    restored = db_session.scalar(select(RecordDeletion).where(RecordDeletion.record_public_ref == ref))
    assert restored is not None
    assert restored.xact_id is None
    assert restored.snapshot == snapshot
    assert restored.reason == "duplicate deposit"

    response = client.get(f"/api/v1/scientific/calculations/{ref}")
    assert response.status_code == 410
    assert response.json()["code"] == "record_deleted"


def test_a_restore_never_reissues_the_id_of_a_deleted_record(client, db_session) -> None:
    """Deleted ids are looked up by ``(record_type, record_id)``; a reissued id would make a live record read as
    deleted. The newest calculation is the deleted one, so the restored rows alone would rewind the sequence below it."""
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("ARCSEQ")))
    make_calculation(db_session, species_entry_id=entry.id)
    ref, _snapshot = _deleted_calculation(db_session)
    deleted_id = db_session.scalar(select(RecordDeletion.record_id).where(RecordDeletion.record_public_ref == ref))
    archive = io.BytesIO()
    write_archive(db_session, archive)
    _empty_archive_tables(db_session)
    restore_archive(db_session, io.BytesIO(archive.getvalue()))

    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("ARCSEQ2")))
    fresh = make_calculation(db_session, species_entry_id=entry.id)

    assert fresh.id > deleted_id
    assert deleted_records(db_session, [(SubmissionRecordType.calculation, fresh.id)]) == {}
    assert client.get(f"/api/v1/scientific/calculations/{fresh.public_ref}").status_code == 200
