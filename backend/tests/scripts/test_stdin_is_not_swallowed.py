"""No script may take its program from stdin, and one that did is proved fixed.

The defect
----------
``conda run`` does not forward the calling shell's stdin to the process it
spawns. So::

    conda run -n tckdb_env python - <<'PY'
    ...
    PY

hands ``python`` an empty stdin: it reads EOF, executes nothing, and exits 0.
The step runs, prints nothing, and is reported green. ``Initialize artifact
bucket`` in both backend workflows was written that way and had therefore
never provisioned a bucket on any green run (#93, fixed in #125).

The same shape does not need ``conda run`` to appear. A heredoc *is* the
process's stdin, so any program read from a heredoc has already consumed the
stream that something else was relying on. ``tckdb_auth.sh``'s
``extract_api_key`` was ``python3 - <<'PY'`` fed by ``printf ... | ...``: the
pipe was discarded, ``json.load(sys.stdin)`` read EOF, and ``create-key``
failed on every valid response while blaming the server for it.

The rule
--------
A program is passed by path (write it to a file and run the file) or by
``-c``. Never by stdin. That removes the whole class rather than the two
instances of it, and it is cheap: both fixes were three lines.

The second half of the rule is that such a step must **print something
specific**, so that "did nothing" and "did the thing" stop looking alike. The
workflows print ``created artifact bucket: <name>`` -- where the word
``created``, rather than ``already present``, is itself the evidence the
bucket had not existed. ``tckdb_auth.sh`` prints the masked key it minted.

This file enforces the first half by scanning, and proves the second half by
running the fixed script for real and reading what it printed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
TCKDB_AUTH = BACKEND_ROOT / "scripts" / "tckdb_auth.sh"

#: An interpreter told to read its program from stdin (``python -``), with the
#: program supplied by a heredoc or by whatever the caller happened to leave on
#: the stream. Both halves of the failure live here: the wrapper that eats
#: stdin, and the heredoc that eats it.
_PROGRAM_FROM_STDIN = re.compile(r"\b(?:python3?|bash|sh)\s+-\s*(?:<<|$)")

#: Backtick-quoted spans are prose *about* the defect -- the comments in the
#: workflows quote the broken form on purpose so the next reader knows what not
#: to write. Quoting it is not doing it.
_BACKTICKED = re.compile(r"`[^`]*`")

_SCANNED_SUFFIXES = {".sh", ".yml", ".yaml", ".md", ".py"}
_SCANNED_NAMES = {"Makefile"}

#: This file names the form in order to forbid it.
_EXEMPT = {Path(__file__).resolve().relative_to(REPO_ROOT)}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(name) for name in out.split("\0") if name]


def _scannable(rel: Path) -> bool:
    if rel in _EXEMPT:
        return False
    return rel.suffix in _SCANNED_SUFFIXES or rel.name in _SCANNED_NAMES


def _offending_lines(text: str) -> list[tuple[int, str]]:
    hits = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.lstrip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        if _PROGRAM_FROM_STDIN.search(_BACKTICKED.sub("", raw)):
            hits.append((lineno, raw.strip()))
    return hits


def test_the_scan_covers_the_files_that_could_carry_the_defect() -> None:
    """A scan that matches no files would pass forever.

    Pinned to floors rather than exact counts so adding a script does not fail
    this for the wrong reason -- the per-file check below is what judges.
    """
    scanned = [rel for rel in _tracked_files() if _scannable(rel)]
    workflows = [rel for rel in scanned if rel.parts[:2] == (".github", "workflows")]
    shell = [rel for rel in scanned if rel.suffix == ".sh"]

    assert len(workflows) >= 5, f"expected the workflows to be scanned, saw {workflows}"
    assert len(shell) >= 10, f"expected the shell scripts to be scanned, saw {len(shell)}"


def test_the_scan_recognises_the_forms_that_actually_shipped() -> None:
    """A pattern that matches nothing real would make the check decorative.

    These are the two lines this repository actually shipped -- the workflow
    step that provisioned no bucket, and the shell function that discarded its
    input -- plus the shapes a fix must be allowed to keep.
    """
    shipped = [
        "          conda run -n tckdb_env python - <<'PY'",
        "    python3 - <<'PY'",
    ]
    for line in shipped:
        assert _offending_lines(line), f"scan missed a form that shipped: {line}"

    allowed = [
        '          conda run -n tckdb_env python "${RUNNER_TEMP}/init_artifact_bucket.py"',
        "    python3 -c 'import json' \"$1\"",
        "        shell: bash -el {0}",
        "          conda run -n tckdb_env python -m pip install --upgrade pip",
        "TCKDB_CCCBDB_LIVE_TESTS=1 conda run -n tckdb_env \\",
        "# NOT piped as `conda run python - <<'PY'`, which runs nothing",
    ]
    for line in allowed:
        assert not _offending_lines(line), f"scan false-positived on: {line}"


def test_no_script_reads_its_program_from_stdin() -> None:
    """Write the program to a file and run it by path, or use ``-c``.

    A heredoc or a stdin-eating wrapper turns "ran and did nothing" into an
    exit status of 0, which no reviewer and no CI badge can tell from success.
    """
    offenders: list[str] = []
    for rel in _tracked_files():
        if not _scannable(rel):
            continue
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in _offending_lines(text):
            offenders.append(f"{rel}:{lineno}: {line}")

    assert offenders == [], (
        "a program is being read from stdin, which silently executes nothing "
        "whenever anything upstream has consumed the stream:\n  "
        + "\n  ".join(offenders)
        + "\nWrite the program to a file and run it by path, or pass it with -c."
    )


# ---------------------------------------------------------------------------
# The script that carried the defect, exercised as a real process
# ---------------------------------------------------------------------------


class _AuthStub:
    """A stand-in for /auth/api-keys that answers with a chosen body."""

    def __init__(self, body: object, code: int = 200) -> None:
        self.body = json.dumps(body).encode("utf-8")
        self.code = code
        self.requests: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                stub.requests.append(self.path)
                self.send_response(stub.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(stub.body)))
                self.end_headers()
                self.wfile.write(stub.body)

            def log_message(self, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _AuthStub:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/api/v1"


def _run_create_key(stub: _AuthStub, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    if shutil.which("curl") is None:
        # Not a skip. A skipped test is silence, and silence reading as
        # success is the entire subject of this file.
        pytest.fail("curl is not installed, so tckdb_auth.sh cannot be exercised")

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    return subprocess.run(
        ["bash", str(TCKDB_AUTH), "create-key", "--name", "regression"],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "TCKDB_BASE_URL": stub.base_url,
            "TCKDB_COOKIE_FILE": str(cookie_file),
            "TCKDB_AUTH_ENV_FILE": str(tmp_path / "auth.env"),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_create_key_extracts_the_key_the_server_returned(tmp_path: Path) -> None:
    """The regression proper: this path could not succeed for any response.

    ``extract_api_key`` read its program from a heredoc, so the piped response
    never reached it and the function returned empty every time. The failure
    surfaced as "could not find an API key field in the response", which points
    at the server. Asserting on the *minted key file* rather than on an exit
    status is what makes this non-vacuous -- the old code also exited, just
    with the wrong status and the wrong story.
    """
    with _AuthStub({"key": "tckdb_sk_regression_value"}) as stub:
        proc = _run_create_key(stub, tmp_path)

    assert proc.returncode == 0, f"create-key failed:\n{proc.stdout}\n{proc.stderr}"
    assert stub.requests == ["/api/v1/auth/api-keys"]

    env_file = tmp_path / "auth.env"
    assert env_file.exists(), "create-key reported success but wrote no env file"
    assert "export TCKDB_API_KEY='tckdb_sk_regression_value'" in env_file.read_text(
        encoding="utf-8"
    )

    # The second half of the rule: the step says what it did, and says enough
    # of it to be checkable. The full key is never printed.
    assert "API key minted" in proc.stdout
    assert "tckdb_sk_regression_value" not in proc.stdout


@pytest.mark.parametrize(
    "field",
    ["key", "api_key", "token", "plain_key", "secret"],
)
def test_create_key_accepts_every_field_name_it_claims_to_try(
    field: str, tmp_path: Path
) -> None:
    """The error message lists five field names; all five must actually work.

    While the function was dead, that list was advertising capability the code
    did not have for any of its entries.
    """
    with _AuthStub({field: f"value_via_{field}"}) as stub:
        proc = _run_create_key(stub, tmp_path)

    assert proc.returncode == 0, f"{field}: {proc.stdout}\n{proc.stderr}"
    assert f"export TCKDB_API_KEY='value_via_{field}'" in (tmp_path / "auth.env").read_text(
        encoding="utf-8"
    )


def test_create_key_still_refuses_a_response_that_carries_no_key(tmp_path: Path) -> None:
    """The honest failure must survive the fix.

    A fix that made the extractor return something for every input would pass
    the test above and break this one, which is the point of keeping both.
    """
    with _AuthStub({"id": 7, "label": "regression"}) as stub:
        proc = _run_create_key(stub, tmp_path)

    assert proc.returncode != 0
    assert "could not find an API key field" in proc.stderr
    assert not (tmp_path / "auth.env").exists()
