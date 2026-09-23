"""No tracked file names one operator's private deployment (issue #521).

The public repository used to hardcode one operator's personal hostname as
the client's default base URL, several ops scripts' default target, and the
worked example in a handful of docs -- and, separately, that operator's
private (Tailscale) network address in a Pi-specific deployment doc. Both
were swept out in the PR that added this file. This is the regression guard:
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
        f"the public repository (issue #521): {hits}. Use a generic "
        "example host (tckdb.example.com / api.tckdb.example.org, matching "
        "docs/deployment/self_hosted_single_node.md's convention), or "
        "require the caller to configure it -- never a hardcoded default."
    )


def test_no_tracked_file_names_the_operators_private_network_address() -> None:
    hits = _grep(_BANNED_ADDRESS)
    assert not hits, (
        "one operator's private (Tailscale) network address was "
        f"reintroduced into the public repository (issue #521): {hits}."
    )


def test_the_banned_strings_are_not_generic_examples() -> None:
    """Precision check on the guard itself: it must not be satisfiable by
    the generic placeholders this PR introduced in their place."""
    assert _BANNED_HOSTNAME not in ("tckdb.example.com", "api.tckdb.example.org")
    assert "example" not in _BANNED_HOSTNAME
    assert _BANNED_ADDRESS != "127.0.0.1"
