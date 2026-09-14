"""``tckdb download artifact <sha256>`` -- the CLI half of artifact access.

The route and the client method both predate this command; what was
missing was a way to reach them without writing Python. These tests pin
the behaviours a thin wrapper can still get wrong.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from tckdb_client import cli
from tckdb_client.errors import TCKDBConnectionError, TCKDBHTTPError

PAYLOAD = b"Gaussian 16 output, verbatim\n"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class _FakeClient:
    """Records what it was asked for and returns canned bytes."""

    last: "_FakeClient | None" = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.asked: list[str] = []
        _FakeClient.last = self

    def download_artifact(self, sha256: str) -> bytes:
        self.asked.append(sha256)
        return PAYLOAD


@pytest.fixture
def fake_client(monkeypatch):
    _FakeClient.last = None
    monkeypatch.setattr(cli, "TCKDBClient", _FakeClient)
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test")
    return _FakeClient


def test_writes_the_bytes_and_passes_the_credential(fake_client, tmp_path, capsys):
    out = tmp_path / "job.log"
    assert cli.main_tckdb(["download", "artifact", DIGEST, "-o", str(out)]) == cli.EXIT_OK

    assert out.read_bytes() == PAYLOAD
    # The route is authenticated; a wrapper that forgot the key would 401
    # in production and pass a test that only checked the file.
    assert _FakeClient.last.kwargs["api_key"] == "tck_test"
    assert _FakeClient.last.asked == [DIGEST]


def test_without_a_credential_it_says_so_instead_of_provoking_a_401(
    fake_client, monkeypatch, tmp_path, capsys
):
    monkeypatch.delenv("TCKDB_API_KEY", raising=False)
    rc = cli.main_tckdb(["download", "artifact", DIGEST, "-o", str(tmp_path / "x")])

    assert rc == cli.EXIT_FAILURES
    assert "require authentication" in capsys.readouterr().err
    # And it did not reach the network to find that out.
    assert _FakeClient.last is None


def test_a_digest_mismatch_is_refused_and_nothing_is_written(
    fake_client, monkeypatch, tmp_path, capsys
):
    """The store is content-addressed, so this is checkable, so it is checked.

    The server verifies the bytes match what IT stored. This verifies they
    match what was ASKED for, which also covers a proxy or cache serving
    the wrong object -- and it runs before the write, so a mismatch never
    lands on disk under a name asserting a digest it does not have.
    """
    class _WrongBytes(_FakeClient):
        def download_artifact(self, sha256: str) -> bytes:
            return b"not the bytes you asked for"

    monkeypatch.setattr(cli, "TCKDBClient", _WrongBytes)
    out = tmp_path / "job.log"

    rc = cli.main_tckdb(["download", "artifact", DIGEST, "-o", str(out)])

    assert rc == cli.EXIT_FAILURES
    assert "digest mismatch" in capsys.readouterr().err
    assert not out.exists()


def test_it_will_not_clobber_an_existing_file_without_force(
    fake_client, tmp_path, capsys
):
    out = tmp_path / "job.log"
    out.write_bytes(b"something the user already had")

    rc = cli.main_tckdb(["download", "artifact", DIGEST, "-o", str(out)])

    assert rc == cli.EXIT_FAILURES
    assert "already exists" in capsys.readouterr().err
    assert out.read_bytes() == b"something the user already had"

    assert cli.main_tckdb(
        ["download", "artifact", DIGEST, "-o", str(out), "--force"]
    ) == cli.EXIT_OK
    assert out.read_bytes() == PAYLOAD


def test_a_directory_target_writes_the_digest_inside_it(fake_client, tmp_path):
    assert cli.main_tckdb(
        ["download", "artifact", DIGEST, "-o", str(tmp_path)]
    ) == cli.EXIT_OK
    # Passing a directory is common and clobbering one is bad, so it means
    # "put it in here" rather than "use this as the filename".
    assert (tmp_path / DIGEST).read_bytes() == PAYLOAD


def test_dash_writes_to_stdout(fake_client, capsysbinary):
    assert cli.main_tckdb(["download", "artifact", DIGEST, "-o", "-"]) == cli.EXIT_OK
    assert capsysbinary.readouterr().out == PAYLOAD


def test_a_ref_instead_of_a_digest_is_rejected_by_name(fake_client, capsys):
    """Pasting `art_...` must not read as "your artifact does not exist"."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main_tckdb(["download", "artifact", "art_abc123"])
    assert exit_info.value.code == cli.EXIT_ARGPARSE
    assert "not a sha256 content digest" in capsys.readouterr().err


def test_an_unreadable_artifact_exits_not_found_and_says_why(
    fake_client, monkeypatch, tmp_path, capsys
):
    class _NotFound(_FakeClient):
        def download_artifact(self, sha256: str) -> bytes:
            raise TCKDBHTTPError("404", status_code=404)

    monkeypatch.setattr(cli, "TCKDBClient", _NotFound)
    rc = cli.main_tckdb(["download", "artifact", DIGEST, "-o", str(tmp_path / "x")])

    assert rc == cli.EXIT_NOT_FOUND
    err = capsys.readouterr().err
    # Both reasons, because the caller cannot tell them apart and the
    # difference changes what they do next.
    assert "not in the archive" in err
    assert "not one of your own deposits" in err


def test_a_connection_failure_is_a_plain_failure(
    fake_client, monkeypatch, tmp_path, capsys
):
    class _Down(_FakeClient):
        def download_artifact(self, sha256: str) -> bytes:
            raise TCKDBConnectionError("connection refused")

    monkeypatch.setattr(cli, "TCKDBClient", _Down)
    rc = cli.main_tckdb(["download", "artifact", DIGEST, "-o", str(tmp_path / "x")])

    assert rc == cli.EXIT_FAILURES
    assert "connection refused" in capsys.readouterr().err
