"""The example systemd units start on the hosts the docs install them on."""

from __future__ import annotations

from pathlib import Path

UNITS = Path(__file__).resolve().parents[3] / "examples" / "deployment" / "systemd"


def _directives(unit: str) -> list[tuple[str, str]]:
    pairs = []
    for line in (UNITS / unit).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith(("#", ";")) and not line.startswith((" ", "\t")):
            key, value = line.split("=", 1)
            pairs.append((key.strip(), value.strip()))
    return pairs


def test_the_api_unit_starts_on_a_host_without_docker() -> None:
    directives = _directives("tckdb-api.service")
    assert not [value for key, value in directives if key in {"Requires", "BindsTo", "Requisite"}]
    assert any(key == "After" and "docker.service" in value.split() for key, value in directives)


def test_the_backup_unit_creates_its_directory_as_root() -> None:
    """The service runs as tckdb, which cannot create a directory under /var/backups."""
    directives = _directives("tckdb-backup.service")
    assert ("User", "tckdb") in directives
    pre = [value for key, value in directives if key == "ExecStartPre"]
    assert pre == ["+/usr/bin/install -d -m 0700 -o tckdb -g tckdb /var/backups/tckdb"]
    assert "mkdir" not in (UNITS / "tckdb-backup.service").read_text()
