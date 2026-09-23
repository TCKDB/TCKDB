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


@pytest.fixture(autouse=True)
def _base_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--base-url`` has no default (issue #521); every call below omits
    it and relies on ``TCKDB_BASE_URL`` instead, set once here so each test
    can stay focused on what it actually exercises. Tests below that care
    about the omitted-entirely case unset this themselves."""
    monkeypatch.setenv("TCKDB_BASE_URL", "http://test.local/api/v1")


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
    # One reason since 2026-09-14. A 404 used to also cover "exists, but
    # not approved and not yours", so the message had to name both and the
    # caller could not tell which applied. Authentication is the whole gate
    # now, so this can be taken literally.
    assert "is in the archive" in err
    assert "deposits" not in err


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
    """``<kind>_<ref><ext>`` -- what the file IS, which record, what format.

    MEASURED on the hosted instance 2026-09-14: 317 artifacts of kind
    ``output_log`` are all called ``input.log``, and 246 of kind
    ``input`` are all called ``input.gjf``. That is Gaussian's
    convention (``g16 input.gjf`` writes ``input.log``), so the recorded
    filename identified nothing and read as the opposite of the truth.
    """

    def test_the_kind_leads(self):
        assert cli.artifact_download_name(
            "art_7k2p9x", "input.log", DIGEST, "output_log"
        ) == "output_log_art_7k2p9x.log"

    def test_the_extension_comes_from_the_name_not_the_kind(self):
        # `output_log` is a role; `.log` is a format.
        assert cli.artifact_download_name(
            "art_a", "job.out", DIGEST, "output_log"
        ) == "output_log_art_a.out"

    def test_two_artifacts_of_one_kind_still_differ(self):
        assert cli.artifact_download_name(
            "art_aaa", "input.log", DIGEST, "output_log"
        ) != cli.artifact_download_name(
            "art_bbb", "input.log", DIGEST, "output_log"
        )

    def test_without_a_kind_the_recorded_stem_leads(self):
        # The measurement is about THIS corpus; a deployment whose
        # filenames do identify something keeps working.
        assert cli.artifact_download_name(
            "art_a", "myjob.log", DIGEST
        ) == "myjob_art_a.log"

    def test_it_falls_back_through_kind_and_name_to_the_digest(self):
        assert cli.artifact_download_name(None, "input.log", DIGEST, "output_log") == "output_log.log"
        assert cli.artifact_download_name(None, "input.log", DIGEST) == "input.log"
        assert cli.artifact_download_name("art_a", None, DIGEST) == DIGEST
        assert cli.artifact_download_name(None, None, DIGEST) == DIGEST
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
    def test_a_stored_filename_cannot_escape_the_output_directory(self, hostile):
        produced = cli.artifact_download_name("art_7k2p9x", hostile, DIGEST, "output_log")
        for forbidden in ["/", "\\", "\x00", ".."]:
            assert forbidden not in produced
        base = Path("/tmp/out")
        assert (base / produced).resolve().parent == base.resolve()

    def test_a_hostile_kind_cannot_escape_either(self):
        # The kind is a server-supplied enum today, but it reaches the
        # name by the same route the filename does.
        produced = cli.artifact_download_name("art_a", "input.log", DIGEST, "../../evil")
        for forbidden in ["/", ".."]:
            assert forbidden not in produced

    def test_a_leading_dot_does_not_survive(self):
        assert not cli.artifact_download_name(None, ".bashrc", DIGEST).startswith(".")


class _NamedClient(_FakeClient):
    def search_artifacts(self, **kwargs):
        self.searched = kwargs
        return {
            "records": [
                {
                    "artifact": {
                        "artifact_ref": "art_7k2p9x",
                        "filename": "input.log",
                        "kind": "output_log",
                    }
                }
            ]
        }


def test_the_default_filename_comes_from_the_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "TCKDBClient", _NamedClient)
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test")
    monkeypatch.chdir(tmp_path)

    assert cli.main_tckdb(["download", "artifact", DIGEST]) == cli.EXIT_OK

    assert (tmp_path / "output_log_art_7k2p9x.log").read_bytes() == PAYLOAD
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


def test_missing_base_url_is_a_clear_error_and_makes_no_request(
    monkeypatch, tmp_path, capsys
):
    """Omitting both ``--base-url`` and ``TCKDB_BASE_URL`` must fail with an
    actionable message naming both ways to supply it -- and must never
    construct a client or make a request to anywhere, least of all a
    hardcoded default host (issue #521)."""
    monkeypatch.setenv("TCKDB_API_KEY", "tck_test")
    monkeypatch.delenv("TCKDB_BASE_URL", raising=False)

    constructed = {"n": 0}

    class _ExplodingClient:
        def __init__(self, *args, **kwargs):
            constructed["n"] += 1
            raise AssertionError(
                "TCKDBClient must never be constructed without a base_url"
            )

    monkeypatch.setattr(cli, "TCKDBClient", _ExplodingClient)

    rc = cli.main_tckdb(
        ["download", "artifact", DIGEST, "-o", str(tmp_path / "x")]
    )

    assert rc == cli.EXIT_FAILURES
    assert constructed["n"] == 0
    err = capsys.readouterr().err
    assert "--base-url" in err
    assert "TCKDB_BASE_URL" in err
