"""Argument parsing for the archive CLI's offline ``verify`` subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import tckdb_archive


def test_parse_verify_arguments() -> None:
    args = tckdb_archive._parse_args(["verify", "archive.tar"])

    assert args.command == "verify"
    assert args.input == Path("archive.tar")


def test_verify_requires_an_existing_file(tmp_path: Path, capsys) -> None:
    assert tckdb_archive.main(["verify", str(tmp_path / "missing.tar")]) == 1
    assert "is not a file" in capsys.readouterr().err


def test_verify_takes_no_overwrite_flag() -> None:
    with pytest.raises(SystemExit):
        tckdb_archive._parse_args(["verify", "archive.tar", "--overwrite"])
