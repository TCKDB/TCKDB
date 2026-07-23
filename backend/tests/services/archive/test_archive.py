from __future__ import annotations

import hashlib
import io
from datetime import datetime

import pytest
from sqlalchemy import cast, func, select, text
from sqlalchemy.sql.sqltypes import Text

from app.db.base import Base
from app.db.models.api_key import ApiKey
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation, CalculationArtifact
from app.db.models.common import (
    AppUserRole,
    CalculationType,
    RecordReviewEventKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry, GeometryAtom
from app.db.models.record_review import RecordReview, RecordReviewEvent
from app.db.models.scientific_record_supersession import ScientificRecordSupersession
from app.db.models.species import Species, SpeciesEntry
from app.db.models.thermo import Thermo
from app.services.archive import (
    ArchiveIntegrityError,
    ArchiveNotEmptyError,
    restore_archive,
    write_archive,
)
from app.services.archive import core as archive_core
from app.services.archive.registry import (
    EXCLUDED_TABLES,
    INCLUDED_TABLES,
    PRESEEDED_TABLES,
    included_tables_in_fk_order,
    validate_registry,
)
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_geometry_atoms,
    attach_output_geometry,
    attach_sp_result,
    make_calculation,
    make_geometry,
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)


def _empty_archive_tables(session) -> None:
    # This helper simulates a fresh migrated target. Replica mode is scoped to
    # the test transaction so append-only/accepted-science triggers do not
    # prevent clearing the source fixture; canonical migration seeds remain.
    session.execute(text("SET LOCAL session_replication_role = replica"))
    for table in reversed(Base.metadata.sorted_tables):
        if table.name not in PRESEEDED_TABLES:
            session.execute(table.delete())
    session.execute(text("SET LOCAL session_replication_role = origin"))
    session.expunge_all()


def test_registry_classifies_every_orm_table_and_codec() -> None:
    validate_registry(Base.metadata)
    assert set(Base.metadata.tables) == set(INCLUDED_TABLES) | set(EXCLUDED_TABLES)
    assert not (set(INCLUDED_TABLES) & set(EXCLUDED_TABLES))
    tables = included_tables_in_fk_order(Base.metadata)
    archive_core._validate_column_codecs(tables)
    names = [table.name for table in tables]
    assert names[-3:] == [
        "record_review",
        "record_review_event",
        "scientific_record_supersession",
    ]


def test_archive_declares_degeneracy_convention_column() -> None:
    kinetics = Base.metadata.tables["kinetics"]
    assert "degeneracy_convention" in archive_core.included_column_names(kinetics)


def test_restore_accepts_exact_fresh_migration_seeds(db_session) -> None:
    _empty_archive_tables(db_session)
    archive_file = io.BytesIO()
    manifest = write_archive(db_session, archive_file)

    report = restore_archive(db_session, io.BytesIO(archive_file.getvalue()))

    assert report.rows_restored == manifest["rows"]["count"]
    locked_relations = set(
        db_session.scalars(
            text(
                """
                SELECT relation.relname
                FROM pg_locks AS lock
                JOIN pg_class AS relation ON relation.oid = lock.relation
                WHERE lock.pid = pg_backend_pid()
                  AND lock.mode = 'ShareLock'
                  AND lock.granted
                """
            )
        ).all()
    )
    assert set(Base.metadata.tables) | {"alembic_version"} <= locked_relations
    for table_name, (identity_names, expected) in PRESEEDED_TABLES.items():
        table = Base.metadata.tables[table_name]
        actual = frozenset(
            tuple(row) for row in db_session.execute(select(*(table.c[name] for name in identity_names)))
        )
        assert actual == expected


def test_write_archive_holds_share_locks_for_the_complete_snapshot(db_session) -> None:
    write_archive(db_session, io.BytesIO())

    locked_relations = set(
        db_session.scalars(
            text(
                """
                SELECT relation.relname
                FROM pg_locks AS lock
                JOIN pg_class AS relation ON relation.oid = lock.relation
                WHERE lock.pid = pg_backend_pid()
                  AND lock.mode = 'ShareLock'
                  AND lock.granted
                """
            )
        ).all()
    )
    assert set(INCLUDED_TABLES) | {"alembic_version"} <= locked_relations


def test_archive_round_trip_preserves_rows_and_artifact_bytes(db_session, monkeypatch) -> None:
    actor = AppUser(
        id=9_000_001,
        username="archive_round_trip_actor",
        email="archive@example.test",
        password_hash="must-not-leave-source-instance",
        role=AppUserRole.curator,
    )
    db_session.add(actor)
    db_session.flush()
    db_session.add(
        ApiKey(
            user_id=actor.id,
            key_hash=hashlib.sha256(b"archive-secret").hexdigest(),
            label="excluded credential",
        )
    )

    species = make_species(
        db_session,
        smiles="[OH2]",
        inchi_key=next_inchi_key("ARCHIVE"),
    )
    entry = make_species_entry(db_session, species)
    entry.created_by = actor.id
    geometry = make_geometry(
        db_session,
        natoms=3,
        xyz_text="3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n",
    )
    attach_geometry_atoms(
        db_session,
        geometry=geometry,
        symbols=["O", "H", "H"],
        coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.9572], [0.0, 0.9266, -0.2396]],
    )
    lot = make_lot(db_session, method="archive_method", basis="archive_basis")
    calculation = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    calculation.created_by = actor.id
    calculation.parameters_json = {
        "grid": "ultrafine",
        "thresholds": [1, 1.25, 2.5],
    }
    attach_sp_result(
        db_session,
        calculation=calculation,
        electronic_energy_hartree=-76.12345678901234,
    )
    attach_output_geometry(db_session, calculation=calculation, geometry=geometry)
    thermo = make_thermo_scalar(
        db_session,
        species_entry=entry,
        h298_kj_mol=-241.826123456789,
        s298_j_mol_k=188.835,
        tmin_k=200.0,
        tmax_k=6000.0,
    )
    thermo.created_by = actor.id
    thermo.h298_uncertainty_kj_mol = 0.012345678901234
    thermo.note = "archive representative thermo"

    replacement_thermo = make_thermo_scalar(
        db_session,
        species_entry=entry,
        h298_kj_mol=-241.7,
        s298_j_mol_k=188.9,
        tmin_k=200.0,
        tmax_k=6000.0,
    )
    replacement_thermo.created_by = actor.id
    replacement_thermo.note = "approved replacement thermo"
    db_session.flush()

    old_first_approved_at = datetime(2025, 1, 2, 3, 4, 5, 123456)
    new_first_approved_at = datetime(2025, 2, 3, 4, 5, 6, 654321)
    old_review = RecordReview(
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        status=RecordReviewStatus.deprecated,
        reviewed_by=actor.id,
        reviewed_at=datetime(2025, 3, 4, 5, 6, 7),
        first_approved_at=old_first_approved_at,
        created_by=actor.id,
    )
    new_review = RecordReview(
        record_type=SubmissionRecordType.thermo,
        record_id=replacement_thermo.id,
        status=RecordReviewStatus.approved,
        reviewed_by=actor.id,
        reviewed_at=new_first_approved_at,
        first_approved_at=new_first_approved_at,
        created_by=actor.id,
    )
    db_session.add_all([old_review, new_review])
    db_session.flush()
    review_events = [
        RecordReviewEvent(
            record_review_id=old_review.id,
            event_kind=RecordReviewEventKind.status_change,
            from_status=RecordReviewStatus.approved,
            to_status=RecordReviewStatus.deprecated,
            actor_user_id=actor.id,
            reason="superseded by corrected thermo",
        ),
        RecordReviewEvent(
            record_review_id=new_review.id,
            event_kind=RecordReviewEventKind.status_change,
            from_status=RecordReviewStatus.under_review,
            to_status=RecordReviewStatus.approved,
            actor_user_id=actor.id,
            reason="replacement approved",
        ),
    ]
    db_session.add_all(review_events)
    supersession = ScientificRecordSupersession(
        record_type=SubmissionRecordType.thermo,
        superseded_record_id=thermo.id,
        superseding_record_id=replacement_thermo.id,
        reason="Corrected thermochemistry",
        created_by=actor.id,
    )
    db_session.add(supersession)

    artifact_content = b"Gaussian output\nbyte-exact archive fixture\n"
    artifact_sha = hashlib.sha256(artifact_content).hexdigest()
    artifact = attach_artifact(
        db_session,
        calculation=calculation,
        filename="job.log",
        uri=f"s3://source/{artifact_sha}",
        sha256=artifact_sha,
        bytes_=len(artifact_content),
    )
    artifact.created_by = actor.id
    db_session.flush()

    original = {
        "actor_id": actor.id,
        "species_id": species.id,
        "species_ref": species.public_ref,
        "entry_id": entry.id,
        "entry_ref": entry.public_ref,
        "entry_mol": db_session.scalar(select(cast(SpeciesEntry.mol, Text)).where(SpeciesEntry.id == entry.id)),
        "geometry_id": geometry.id,
        "calculation_id": calculation.id,
        "calculation_ref": calculation.public_ref,
        "thermo_id": thermo.id,
        "replacement_thermo_id": replacement_thermo.id,
        "thermo_h298_hex": thermo.h298_kj_mol.hex(),
        "thermo_uncertainty_hex": thermo.h298_uncertainty_kj_mol.hex(),
        "artifact_id": artifact.id,
        "old_review_id": old_review.id,
        "new_review_id": new_review.id,
        "supersession_id": supersession.id,
    }

    monkeypatch.setattr(
        archive_core,
        "load_artifact_bytes",
        lambda sha256, *, expected_bytes=None: artifact_content,
    )
    first = io.BytesIO()
    second = io.BytesIO()
    manifest = write_archive(db_session, first)
    write_archive(db_session, second)
    archive_bytes = first.getvalue()
    assert archive_bytes == second.getvalue()
    assert manifest["schema"] == "tckdb.archive.v1"
    assert manifest["rows"]["count"] > 0
    assert manifest["blobs"] == [
        {
            "path": f"blobs/{artifact_sha}",
            "sha256": artifact_sha,
            "bytes": len(artifact_content),
        }
    ]

    restored_objects: dict[str, bytes] = {}

    def _store(content: bytes, sha256: str) -> str:
        restored_objects[sha256] = content
        return f"s3://restored/{sha256[:2]}/{sha256}"

    monkeypatch.setattr(archive_core, "store_artifact", _store)
    _empty_archive_tables(db_session)
    report = restore_archive(db_session, io.BytesIO(archive_bytes))

    assert report.rows_restored == manifest["rows"]["count"]
    assert report.blobs_restored == 1
    assert restored_objects == {artifact_sha: artifact_content}
    assert db_session.scalar(select(func.count()).select_from(ApiKey)) == 0

    restored_actor = db_session.get(AppUser, original["actor_id"])
    assert restored_actor is not None
    assert restored_actor.username == "archive_round_trip_actor"
    assert restored_actor.password_hash is None

    restored_species = db_session.get(Species, original["species_id"])
    assert restored_species is not None
    assert restored_species.public_ref == original["species_ref"]
    restored_entry = db_session.get(SpeciesEntry, original["entry_id"])
    assert restored_entry is not None
    assert restored_entry.public_ref == original["entry_ref"]
    assert (
        db_session.scalar(select(cast(SpeciesEntry.mol, Text)).where(SpeciesEntry.id == original["entry_id"]))
        == original["entry_mol"]
    )

    restored_geometry = db_session.get(Geometry, original["geometry_id"])
    assert restored_geometry is not None
    atoms = db_session.scalars(
        select(GeometryAtom).where(GeometryAtom.geometry_id == restored_geometry.id).order_by(GeometryAtom.atom_index)
    ).all()
    assert [(atom.element.strip(), atom.x.hex(), atom.y.hex(), atom.z.hex()) for atom in atoms] == [
        ("O", (0.0).hex(), (0.0).hex(), (0.0).hex()),
        ("H", (0.0).hex(), (0.0).hex(), (0.9572).hex()),
        ("H", (0.0).hex(), (0.9266).hex(), (-0.2396).hex()),
    ]

    restored_calculation = db_session.get(Calculation, original["calculation_id"])
    assert restored_calculation is not None
    assert restored_calculation.public_ref == original["calculation_ref"]
    assert restored_calculation.parameters_json == calculation.parameters_json
    restored_thermo = db_session.get(Thermo, original["thermo_id"])
    assert restored_thermo is not None
    assert restored_thermo.h298_kj_mol.hex() == original["thermo_h298_hex"]
    assert restored_thermo.h298_uncertainty_kj_mol.hex() == original["thermo_uncertainty_hex"]
    restored_old_review = db_session.get(RecordReview, original["old_review_id"])
    restored_new_review = db_session.get(RecordReview, original["new_review_id"])
    assert restored_old_review is not None
    assert restored_old_review.status is RecordReviewStatus.deprecated
    assert restored_old_review.first_approved_at == old_first_approved_at
    assert restored_new_review is not None
    assert restored_new_review.status is RecordReviewStatus.approved
    assert restored_new_review.first_approved_at == new_first_approved_at
    restored_supersession = db_session.get(
        ScientificRecordSupersession,
        original["supersession_id"],
    )
    assert restored_supersession is not None
    assert restored_supersession.superseded_record_id == original["thermo_id"]
    assert restored_supersession.superseding_record_id == original["replacement_thermo_id"]
    restored_fixture_event_count = db_session.scalar(
        select(func.count())
        .select_from(RecordReviewEvent)
        .where(
            RecordReviewEvent.record_review_id.in_(
                [original["old_review_id"], original["new_review_id"]]
            )
        )
    )
    assert restored_fixture_event_count == 2
    restored_artifact = db_session.get(CalculationArtifact, original["artifact_id"])
    assert restored_artifact is not None
    assert restored_artifact.uri == f"s3://restored/{artifact_sha[:2]}/{artifact_sha}"

    new_species = make_species(
        db_session,
        smiles="[NH3]",
        inchi_key=next_inchi_key("POSTARCHIVE"),
    )
    assert new_species.id > original["species_id"]


def test_restore_rejects_tampered_rows_before_writing(db_session, monkeypatch) -> None:
    archive_file = io.BytesIO()
    write_archive(db_session, archive_file)
    tampered = bytearray(archive_file.getvalue())
    marker = b'"record_type":"row"'
    offset = tampered.find(marker)
    assert offset >= 0
    tampered[offset + len(marker) - 4 : offset + len(marker) - 1] = b"rox"

    stored: list[str] = []
    monkeypatch.setattr(
        archive_core,
        "store_artifact",
        lambda _content, sha256: stored.append(sha256),
    )
    with pytest.raises(ArchiveIntegrityError, match="checksum mismatch"):
        restore_archive(db_session, io.BytesIO(tampered))
    assert stored == []


def test_restore_requires_an_empty_target(db_session) -> None:
    archive_file = io.BytesIO()
    write_archive(db_session, archive_file)
    make_species(
        db_session,
        smiles="[He]",
        inchi_key=next_inchi_key("NONEMPTY"),
    )

    with pytest.raises(ArchiveNotEmptyError, match="Target contains rows"):
        restore_archive(db_session, io.BytesIO(archive_file.getvalue()))


def test_restore_rejects_migration_seed_identity_drift(db_session) -> None:
    _empty_archive_tables(db_session)
    archive_file = io.BytesIO()
    write_archive(db_session, archive_file)
    reaction_family = Base.metadata.tables["reaction_family"]
    db_session.execute(reaction_family.insert().values(name="noncanonical_archive_test_family"))

    with pytest.raises(ArchiveNotEmptyError, match="migration seeds differ"):
        restore_archive(db_session, io.BytesIO(archive_file.getvalue()))


def test_restore_rejects_excluded_operational_rows(db_session) -> None:
    _empty_archive_tables(db_session)
    archive_file = io.BytesIO()
    write_archive(db_session, archive_file)
    upload_job = Base.metadata.tables["upload_job"]
    db_session.execute(upload_job.insert().values(kind="thermo", payload={}))

    with pytest.raises(ArchiveNotEmptyError, match="'upload_job'"):
        restore_archive(db_session, io.BytesIO(archive_file.getvalue()))
