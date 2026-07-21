"""Argument and filesystem-safety tests for the archive CLI wrapper."""

from __future__ import annotations

from pathlib import Path

from scripts import tckdb_archive


def test_parse_create_arguments() -> None:
    args = tckdb_archive._parse_args(["create", "archive.tar", "--overwrite"])

    assert args.command == "create"
    assert args.output == Path("archive.tar")
    assert args.overwrite is True


def test_parse_restore_arguments() -> None:
    args = tckdb_archive._parse_args(["restore", "archive.tar"])

    assert args.command == "restore"
    assert args.input == Path("archive.tar")


def test_create_writes_archive_and_reports_manifest(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "archive.tar"

    class FakeSession:
        rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def rollback(self) -> None:
            self.rolled_back = True

    session = FakeSession()

    def fake_write_archive(received_session, destination: Path):
        assert received_session is session
        destination.write_bytes(b"archive")
        return {
            "database_revisions": ["revision-1"],
            "rows": {"count": 12},
            "blobs": [{"sha256": "digest"}],
        }

    monkeypatch.setattr(tckdb_archive, "SessionLocal", lambda: session)
    monkeypatch.setattr(tckdb_archive, "write_archive", fake_write_archive)

    assert tckdb_archive.main(["create", str(output)]) == 0
    assert output.read_bytes() == b"archive"
    assert session.rolled_back is True
    assert "Rows: 12  Blobs: 1  Revision: revision-1" in capsys.readouterr().out


def test_create_rejects_existing_output_before_opening_database(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output = tmp_path / "archive.tar"
    output.write_bytes(b"existing")
    monkeypatch.setattr(
        tckdb_archive,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database opened")),
    )

    assert tckdb_archive.main(["create", str(output)]) == 1
    assert output.read_bytes() == b"existing"
    assert "pass --overwrite" in capsys.readouterr().err


def test_restore_rejects_missing_input_before_opening_database(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "missing.tar"
    monkeypatch.setattr(
        tckdb_archive,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database opened")),
    )

    assert tckdb_archive.main(["restore", str(source)]) == 1
    assert "is not a file" in capsys.readouterr().err
