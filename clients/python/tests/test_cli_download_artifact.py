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


class TestArtifactDownloadName:
    """``<artifact_ref>_<filename>`` -- unique, and it keeps the extension.

    A bare 64-character digest is unique too, and unusable: it is the
    fallback, never the goal.
    """

    def test_ref_and_filename_are_joined(self):
        assert cli.artifact_download_name(
            "art_7k2p9x", "input.log", DIGEST
        ) == "input_art_7k2p9x.log"

    def test_the_extension_survives_so_the_file_opens_in_the_right_thing(self):
        # The ref goes before the suffix, not after the whole filename.
        # `input.log_art_7k2p9x` disambiguates just as well and is no
        # longer a `.log`.
        for name, expected_suffix in [
            ("input.log", ".log"),
            ("job.out.log", ".log"),
            ("geom.xyz", ".xyz"),
        ]:
            assert cli.artifact_download_name("art_a", name, DIGEST).endswith(
                expected_suffix
            )

    def test_a_name_with_no_extension_just_gets_the_ref(self):
        assert cli.artifact_download_name("art_a", "OUTPUT", DIGEST) == "OUTPUT_art_a"

    def test_the_ref_is_what_stops_two_input_logs_colliding(self):
        first = cli.artifact_download_name("art_aaa", "input.log", DIGEST)
        second = cli.artifact_download_name("art_bbb", "input.log", DIGEST)
        assert first != second

    def test_filename_alone_when_there_is_no_ref(self):
        assert cli.artifact_download_name(None, "input.log", DIGEST) == "input.log"

    def test_the_digest_is_the_fallback_and_only_the_fallback(self):
        assert cli.artifact_download_name(None, None, DIGEST) == DIGEST
        assert cli.artifact_download_name("art_7k2p9x", None, DIGEST) == DIGEST
        assert cli.artifact_download_name(None, "   ", DIGEST) == DIGEST
        # A name made entirely of characters that cannot survive sanitising
        # leaves nothing to use.
        assert cli.artifact_download_name(None, "///", DIGEST) == DIGEST

    @pytest.mark.parametrize(
        "hostile",
        [
            "../../.ssh/authorized_keys",
            "/etc/passwd",
            "..\\..\\windows\\system32\\config",
            "sub/dir/input.log",
            "a\x00b.log",
        ],
    )
    def test_a_server_supplied_name_cannot_escape_the_output_directory(self, hostile):
        """The filename is stored data, so it is not trusted.

        Joined naively to an output directory, any of these would write
        somewhere the caller did not name.
        """
        produced = cli.artifact_download_name("art_7k2p9x", hostile, DIGEST)

        assert "/" not in produced
        assert "\\" not in produced
        assert "\x00" not in produced
        assert ".." not in produced
        # And the result stays inside the directory it is joined to.
        base = Path("/tmp/out")
        assert (base / produced).resolve().parent == base.resolve()

    def test_a_leading_dot_does_not_survive(self):
        # ``.bashrc`` written into a directory is a hidden file; the archive
        # should not be able to make one by naming an artifact that way.
        assert not cli.artifact_download_name(None, ".bashrc", DIGEST).startswith(".")


class _NamedClient(_FakeClient):
    def search_artifacts(self, **kwargs):
        self.searched = kwargs
        return {
            "records": [
                {"artifact": {"artifact_ref": "art_7k2p9x", "filename": "input.log"}}
            ]
        }


def test_the_default_filename_comes_from_the_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "TCKDBClient", _NamedClient)
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test")
    monkeypatch.chdir(tmp_path)

    assert cli.main_tckdb(["download", "artifact", DIGEST]) == cli.EXIT_OK

    assert (tmp_path / "input_art_7k2p9x.log").read_bytes() == PAYLOAD
    assert not (tmp_path / DIGEST).exists()
    assert _FakeClient.last.searched["sha256"] == DIGEST


def test_an_explicit_output_skips_the_lookup_entirely(monkeypatch, tmp_path):
    """`--output` is an answer; a request to second-guess it is waste."""
    monkeypatch.setattr(cli, "TCKDBClient", _NamedClient)
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test")
    out = tmp_path / "mine.log"

    assert cli.main_tckdb(
        ["download", "artifact", DIGEST, "-o", str(out)]
    ) == cli.EXIT_OK

    assert out.read_bytes() == PAYLOAD
    assert not hasattr(_FakeClient.last, "searched")


def test_a_failed_name_lookup_still_downloads(monkeypatch, tmp_path):
    """Naming a file well is not worth failing a download over."""
    class _SearchBroken(_FakeClient):
        def search_artifacts(self, **kwargs):
            raise TCKDBConnectionError("search is down")

    monkeypatch.setattr(cli, "TCKDBClient", _SearchBroken)
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test")
    monkeypatch.chdir(tmp_path)

    assert cli.main_tckdb(["download", "artifact", DIGEST]) == cli.EXIT_OK
    assert (tmp_path / DIGEST).read_bytes() == PAYLOAD


def test_a_directory_target_uses_the_derived_name_inside_it(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "TCKDBClient", _NamedClient)
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test")

    assert cli.main_tckdb(
        ["download", "artifact", DIGEST, "-o", str(tmp_path)]
    ) == cli.EXIT_OK
    assert (tmp_path / DIGEST).read_bytes() == PAYLOAD


def test_the_missing_credential_message_never_echoes_what_was_passed(
    monkeypatch, tmp_path, capsys
):
    """`--api-key-env` takes a variable NAME, but a caller can get that wrong.

    `--api-key-env "$TCKDB_API_KEY"` passes the key itself. Echoing the
    argument back in the error would then print the secret to stderr,
    into CI logs and shell scrollback. Naming the default instead is just
    as actionable and cannot leak.
    """
    monkeypatch.setattr(cli, "TCKDBClient", _FakeClient)
    monkeypatch.delenv("TCKDB_API_KEY", raising=False)
    secret = "tck_pretend_this_is_a_real_key"

    rc = cli.main_tckdb([
        "download", "artifact", DIGEST, "--api-key-env", secret,
        "-o", str(tmp_path / "x"),
    ])

    assert rc == cli.EXIT_FAILURES
    err = capsys.readouterr().err
    assert secret not in err
    assert "TCKDB_API_KEY" in err
