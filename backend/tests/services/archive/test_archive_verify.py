"""Offline archive verification, and the fresh-target tolerance it revealed.

``verify_archive`` answers "are these the bytes the writer produced?" with no
database in hand. The tamper tests rebuild the tar with one byte changed or
one member added, so the check is shown to discriminate rather than to pass.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from sqlalchemy import text

from app.db.base import Base
from app.services.archive import restore_archive, verify_archive, write_archive
from app.services.archive.registry import MIGRATION_WRITTEN_TABLES, PRESEEDED_TABLES
from scripts import tckdb_archive


def _rebuild(source: Path, target: Path, *, mutate: dict[str, bytes] | None = None, add: dict[str, bytes] | None = None) -> None:
    with tarfile.open(source, "r:*") as reader, tarfile.open(target, "w") as writer:
        for member in reader.getmembers():
            content = reader.extractfile(member).read()
            if mutate and member.name in mutate:
                content = mutate[member.name]
            info = tarfile.TarInfo(member.name)
            info.size = len(content)
            writer.addfile(info, io.BytesIO(content))
        for name, content in (add or {}).items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            writer.addfile(info, io.BytesIO(content))


def test_verify_archive_passes_a_fresh_archive(db_session, tmp_path):
    path = tmp_path / "a.tar"
    manifest = write_archive(db_session, path)
    report = verify_archive(path)
    assert report.ok, report.problems
    assert report.schema == "tckdb.archive.v1"
    assert report.database_revisions == manifest["database_revisions"]
    assert report.rows_declared == manifest["rows"]["count"]
    assert report.members_checked == 1 + len(manifest["blobs"])
    assert tckdb_archive.main(["verify", str(path)]) == 0


def test_verify_archive_detects_a_flipped_byte_in_rows(db_session, tmp_path, capsys):
    path = tmp_path / "a.tar"
    write_archive(db_session, path)
    with tarfile.open(path, "r:*") as reader:
        rows = reader.extractfile("rows.ndjson").read()
    assert rows, "an archive of the test corpus must carry rows"
    flipped = bytearray(rows)
    flipped[0] ^= 0x01
    tampered = tmp_path / "tampered.tar"
    _rebuild(path, tampered, mutate={"rows.ndjson": bytes(flipped)})

    report = verify_archive(tampered)
    assert not report.ok
    assert any("rows.ndjson hashes to" in p for p in report.problems)
    assert tckdb_archive.main(["verify", str(tampered)]) == 1
    assert "rows.ndjson hashes to" in capsys.readouterr().err


def test_verify_archive_detects_an_undeclared_member(db_session, tmp_path):
    path = tmp_path / "a.tar"
    write_archive(db_session, path)
    tampered = tmp_path / "tampered.tar"
    _rebuild(path, tampered, add={"blobs/" + "f" * 64: b"stray"})
    report = verify_archive(tampered)
    assert not report.ok
    assert any("undeclared members" in p for p in report.problems)


def test_verify_archive_rejects_a_foreign_manifest(db_session, tmp_path):
    path = tmp_path / "a.tar"
    write_archive(db_session, path)
    tampered = tmp_path / "tampered.tar"
    _rebuild(path, tampered, mutate={"manifest.json": b'{"schema": "something.else"}'})
    report = verify_archive(tampered)
    assert not report.ok
    assert any("manifest schema is 'something.else'" in p for p in report.problems)


def _empty_archive_tables(session) -> None:
    session.execute(text("SET LOCAL session_replication_role = replica"))
    for table in reversed(Base.metadata.sorted_tables):
        if table.name not in PRESEEDED_TABLES:
            session.execute(table.delete())
    session.execute(text("SET LOCAL session_replication_role = origin"))
    session.expunge_all()


def test_restore_accepts_a_target_holding_migration_written_repair_declarations(db_session):
    """A database made by ``alembic upgrade head`` alone holds these rows; restore must not refuse it."""
    assert "accepted_science_repair" in MIGRATION_WRITTEN_TABLES
    _empty_archive_tables(db_session)
    archive_file = io.BytesIO()
    manifest = write_archive(db_session, archive_file)
    db_session.execute(
        text(
            """
            INSERT INTO accepted_science_repair (target_table, declared_columns, alembic_revision, reason)
            VALUES ('thermo', ARRAY['model_kind'], 'testrevision', 'declared by a migration on an empty database')
            """
        )
    )
    declared = db_session.scalar(text("SELECT count(*) FROM accepted_science_repair"))
    assert declared == 1

    report = restore_archive(db_session, io.BytesIO(archive_file.getvalue()))

    assert report.rows_restored == manifest["rows"]["count"]
    # The declaration is neither archived nor removed: it stays with the deployment.
    assert db_session.scalar(text("SELECT count(*) FROM accepted_science_repair")) == 1
