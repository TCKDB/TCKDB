"""Every operator shell script answers ``--help`` before it does anything else.

``--help`` (and ``-h``) prints usage on stdout and exits 0 with none of the
script's environment variables set, and runs no ``docker``, ``curl``,
``ssh``, ``pg_dump``, ``psql``, ``systemctl``, ``sudo`` or ``iptables``:
each is a stub on ``PATH`` that writes a marker file when called.

Scripts whose usage is their header comment must also name every
``TCKDB_*`` variable they read, so the header stays the one place the
settings are listed.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

OPS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "ops"
SCRIPTS = sorted(OPS_DIR.glob("*.sh"))

#: Scripts that print their whole header comment as usage. The storage
#: firewall script prints only its synopsis lines.
HEADER_USAGE = {
    "tckdb_alert_check.sh",
    "tckdb_backup.sh",
    "tckdb_ci_watchdog.sh",
    "tckdb_deploy.sh",
    "tckdb_make_env.sh",
}

STUBBED = ("docker", "curl", "ssh", "pg_dump", "psql", "systemctl", "sudo", "iptables", "ip6tables")


@pytest.fixture
def sandbox(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "called"
    for tool in STUBBED:
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{marker}"\nexit 0\n')
        stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    env = {"PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(home)}
    return env, marker, home, work


def test_every_ops_shell_script_is_covered():
    assert {s.name for s in SCRIPTS} >= HEADER_USAGE | {"tckdb_storage_hostfw.sh"}


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
@pytest.mark.parametrize("args", [["--help"], ["-h"], ["--bogus", "--help"]], ids=["long", "short", "after"])
def test_help_prints_usage_and_does_nothing_else(script, args, sandbox):
    if script.name == "tckdb_deploy.sh" and args[0] == "--bogus":
        args = ["sha-0000000", "--help"]
    elif args[0] == "--bogus":
        # These scripts refuse an unknown flag at the point they meet it.
        if script.name in {"tckdb_make_env.sh", "tckdb_storage_hostfw.sh"}:
            pytest.skip("unknown flags before --help keep their usage-error behaviour")
    env, marker, home, work = sandbox
    proc = subprocess.run(
        ["bash", str(script), *args],
        env=env,
        cwd=work,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert script.name in proc.stdout
    assert not proc.stdout.lstrip().startswith("#")
    assert not marker.exists(), marker.read_text()
    assert list(home.iterdir()) == []
    assert list(work.iterdir()) == []


@pytest.mark.parametrize("name", sorted(HEADER_USAGE))
def test_header_usage_names_every_variable_the_script_reads(name, sandbox):
    script = OPS_DIR / name
    code = "\n".join(line for line in script.read_text().splitlines() if not line.lstrip().startswith("#"))
    read = set(re.findall(r"\$\{(TCKDB_[A-Z0-9_]+)", code))
    assert read, f"{name} reads no TCKDB_* variable; the pattern is wrong"
    env, _, _, work = sandbox
    out = subprocess.run(
        ["bash", str(script), "--help"], env=env, cwd=work, capture_output=True, text=True, timeout=30
    ).stdout
    assert {var for var in read if var not in out} == set()
    assert "USAGE" in out
