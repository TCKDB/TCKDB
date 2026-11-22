"""No tracked file names one operator's private deployment.

The public repository must not hardcode one operator's personal hostname as
the client's default base URL, several ops scripts' default target, or the
worked example in docs -- or that operator's private (Tailscale) network address
in a Pi-specific deployment doc. This is the regression guard:
if either string is ever reintroduced into a tracked file, this test fails.

WHY THE TARGET STRINGS ARE BUILT RATHER THAN WRITTEN LITERALLY
    This file scans every tracked file in the repository, including itself.
    A guard that spelled out the banned hostname or address as a plain
    string literal would match its own source and fail permanently -- the
    same trap a guard against a literal `--` or a literal secret would fall
    into if it were not careful. Building each target from parts keeps this
    file itself clean of the string it exists to forbid, so no self-exclusion
    is needed and the check stays precise: it looks for the exact string,
    not a shape that could also match an unrelated generic example such as
    `tckdb.example.com` or `100.64.0.0/10` in the abstract.

WHAT COUNTS AS "TRACKED"
    `git ls-files` from the repository root -- the same notion of "in the
    repository" the rest of the codebase uses (see
    `backend/tests/ops/conftest.py`'s `WORKFLOWS_DIR`, which climbs the same
    four `parents[]` levels from this directory to the repo root). Untracked
    scratch files, build output, and anything under `.git/` are irrelevant:
    this guard is about what ships to every clone and fork, not about a
    contributor's local working tree.

    A file that cannot be decoded as UTF-8 is skipped rather than failed --
    it is binary (an image, a compiled asset), and a hostname cannot hide
    inside a byte sequence that was never trying to be text in the first
    place.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Built from parts so this file's own source never contains the literal
#: banned hostname -- see the module docstring.
_BANNED_HOSTNAME = "tckdb." + "home" + "calvin" + ".com"

#: The operator's private Tailscale address, built from octets for the same
#: reason. A literal-string match, not a CGNAT range regex: a range would
#: also flag an unrelated, legitimate example address in that space.
_BANNED_ADDRESS = ".".join(str(octet) for octet in (100, 85, 114, 78))


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    raw = result.stdout.decode("utf-8")
    return [REPO_ROOT / entry for entry in raw.split("\0") if entry]


def _grep(needle: str) -> dict[str, list[int]]:
    """path (relative to the repo root) -> 1-indexed line numbers containing
    ``needle``, for every tracked, UTF-8-decodable file."""
    hits: dict[str, list[int]] = {}
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            # Binary asset, or a path git recorded (e.g. a submodule gitlink)
            # that is not a plain readable file. Neither can carry the
            # string as text.
            continue
        lines = [
            number
            for number, line in enumerate(text.splitlines(), start=1)
            if needle in line
        ]
        if lines:
            hits[str(path.relative_to(REPO_ROOT))] = lines
    return hits


def test_no_tracked_file_names_the_operators_hostname() -> None:
    hits = _grep(_BANNED_HOSTNAME)
    assert not hits, (
        "one operator's private deployment hostname was reintroduced into "
        f"the public repository: {hits}. Use a generic "
        "example host (tckdb.example.com / api.tckdb.example.org, matching "
        "docs/deployment/self_hosted_single_node.md's convention), or "
        "require the caller to configure it -- never a hardcoded default."
    )


def test_no_tracked_file_names_the_operators_private_network_address() -> None:
    hits = _grep(_BANNED_ADDRESS)
    assert not hits, (
        "one operator's private (Tailscale) network address was "
        f"reintroduced into the public repository: {hits}."
    )


def test_the_banned_strings_are_not_generic_examples() -> None:
    """Precision check on the guard itself: it must not be satisfiable by
    the generic placeholders that replace them."""
    assert _BANNED_HOSTNAME not in ("tckdb.example.com", "api.tckdb.example.org")
    assert "example" not in _BANNED_HOSTNAME
    assert _BANNED_ADDRESS != "127.0.0.1"


# ---------------------------------------------------------------------------
# The self-host bundle
# ---------------------------------------------------------------------------
#
# Every file make_selfhost_bundle.sh ships goes to hosts that are not the
# maintainer's, so none may carry one host's env file name, a home directory
# or the maintainer host's compose project -- except where a script keeps
# that project as its documented fallback for the host it was written on.

_BUNDLE_SCRIPT = REPO_ROOT / "backend/scripts/ops/make_selfhost_bundle.sh"

#: The maintainer host's compose project, built from parts like the strings above.
_LEGACY_PROJECT = "tckdb" + "v2"

_DEPLOY = "backend/scripts/ops/tckdb_deploy.sh"
_BACKUP = "backend/scripts/ops/tckdb_backup.sh"

#: (file, stripped line) pairs allowed to name the legacy project: the db
#: container and network a script falls back to when no project resolves,
#: so the host that sets nothing else keeps working, and their documentation.
_LEGACY_ALLOWED = {
    (_DEPLOY, f'DEFAULT_DB_CONTAINER="{_LEGACY_PROJECT}-db-1"'),
    (_DEPLOY, f'DEFAULT_DB_NETWORK="{_LEGACY_PROJECT}_default"'),
    (_DEPLOY, f"#   service and `<project>_default`); the container `{_LEGACY_PROJECT}-db-1` if it"),
    (_DEPLOY, f"#   else `{_LEGACY_PROJECT}-db-1` / `{_LEGACY_PROJECT}_default`."),
    (_BACKUP, f"LEGACY_CONTAINER={_LEGACY_PROJECT}-db-1"),
    (_BACKUP, f"#   the container `{_LEGACY_PROJECT}-db-1` if it exists; else `docker compose ps -q db`"),
    (_BACKUP, f"#   run here, given TCKDB_ENV_FILE; else `{_LEGACY_PROJECT}-db-1`."),
    (_BACKUP, f"#   With only TCKDB_BACKUP_DEST set and `{_LEGACY_PROJECT}-db-1` present, this dumps"),
    (_BACKUP, f"#   database `tckdb` as role `tckdb` in `{_LEGACY_PROJECT}-db-1`."),
}

#: One host's env file name, and any home directory but the placeholder.
_BUNDLE_BANNED = (
    re.compile(re.escape(".env" + ".pi")),
    re.compile(r"/home/(?!YOUR_USER/)"),
)


def _bundled_files() -> list[str]:
    text = _BUNDLE_SCRIPT.read_text()
    block = re.search(r"^FILES=\(\n(.*?)^\)", text, re.M | re.S)
    assert block, "make_selfhost_bundle.sh has no FILES=( ... ) list"
    files = [line.strip() for line in block.group(1).splitlines() if line.strip()]
    assert "docker-compose.yml" in files and len(files) > 10, files
    # The script writes the bundle's README.md and VERSION itself.
    return files + ["backend/scripts/ops/make_selfhost_bundle.sh"]


def _bundle_lines():
    for rel in _bundled_files():
        for number, line in enumerate((REPO_ROOT / rel).read_text().splitlines(), start=1):
            yield rel, number, line


def test_bundled_files_name_no_host_env_file_or_home_directory() -> None:
    hits = [
        f"{rel}:{number}: {line.strip()}"
        for rel, number, line in _bundle_lines()
        if any(pattern.search(line) for pattern in _BUNDLE_BANNED)
    ]
    assert not hits, hits


def test_bundled_files_name_the_legacy_project_only_as_the_fallback() -> None:
    hits = [
        f"{rel}:{number}: {line.strip()}"
        for rel, number, line in _bundle_lines()
        if _LEGACY_PROJECT in line and (rel, line.strip()) not in _LEGACY_ALLOWED
    ]
    assert not hits, hits


def test_every_legacy_allowance_is_still_in_use() -> None:
    """A stale allowance would let the string back in unnoticed."""
    present = {(rel, line.strip()) for rel, _, line in _bundle_lines()}
    assert _LEGACY_ALLOWED <= present, _LEGACY_ALLOWED - present


def test_the_bundle_guards_catch_what_they_name() -> None:
    assert any(p.search("TCKDB_ENV_FILE=" + ".env" + ".pi") for p in _BUNDLE_BANNED)
    assert any(p.search("/home/" + "calvin/tckdb") for p in _BUNDLE_BANNED)
    assert not any(p.search("EnvironmentFile=/home/YOUR_USER/.config/x.env") for p in _BUNDLE_BANNED)
