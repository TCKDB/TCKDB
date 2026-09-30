"""Every ``python3 -c '...'`` program embedded in a backend shell script compiles.

The defect: ``scripts/dev_login.sh`` embedded
``print(f"{data[\"username\"]} ...")``. A backslash inside an f-string
expression is a SyntaxError on every interpreter before 3.12, and 3.13/3.14
reject this exact spelling too. ``bash -n`` cannot see it (the program is just
a quoted string to bash) and the script only reached that line after a real
login, so the failure surfaced as a broken script at the very end of a
successful login.

Two checks, because they catch different things:

* compile every embedded program (catches the whole class, in any script);
* run the ``dev_login.sh`` identity program on sample responses (proves the
  fixed program prints what the operator is meant to see).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

# ``python3 -c '<program>'`` where the program is single-quoted, so bash passes
# it through byte for byte (no escapes are processed inside single quotes).
_EMBEDDED = re.compile(r"python3? -c '([^']*)'")


def _embedded_programs() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for script in sorted(_SCRIPTS.rglob("*.sh")):
        for i, match in enumerate(_EMBEDDED.finditer(script.read_text())):
            found.append((f"{script.relative_to(_SCRIPTS)}#{i}", match.group(1)))
    return found


def test_the_scan_finds_the_embedded_programs() -> None:
    """A scan that matches nothing would make the compile test vacuous."""
    ids = {name for name, _ in _embedded_programs()}
    assert "dev_login.sh#0" in ids
    assert len(ids) >= 4, ids


@pytest.mark.parametrize(
    ("name", "program"), _embedded_programs(), ids=[n for n, _ in _embedded_programs()]
)
def test_embedded_python_compiles(name: str, program: str) -> None:
    compile(program, name, "exec")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ('{"username": "calvin", "role": "admin"}', "calvin (role=admin)"),
        ('{"username": "calvin"}', "calvin"),
        ("not json", "(identity not reported by the server)"),
    ],
)
def test_dev_login_identity_program_prints_the_identity(
    response: str, expected: str
) -> None:
    program = dict(_embedded_programs())["dev_login.sh#0"]
    out = subprocess.run(
        [sys.executable, "-c", program, response],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == expected
