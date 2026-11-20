"""``tckdb_deploy.sh`` finds the database of whichever compose project it serves.

What is pinned here:

* with nothing new set, the backup and the networks are exactly what they
  were: ``pg_dump -U tckdb -d tckdb`` in ``tckdbv2-db-1``, on ``tckdbv2_default``;
* the backup's role and database come from the env files (``DB_ADMIN_USER``
  from the migration file first, ``DB_NAME`` from the API's file);
* ``COMPOSE_PROJECT_NAME`` picks that project's ``db`` and ``<project>_default``,
  and a project with no ``db`` stops the deploy before anything changes;
* without either, a host whose ``db`` is not ``tckdbv2-db-1`` is found through
  ``docker compose ps -q db`` and its project label;
* ``--check`` prints one value per line, never a value and its fallback.

The ``docker`` and ``curl`` fakes are the ones the extra-networks test uses.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.ops import test_api_deploy_extra_networks as _base

_UNTOUCHED = _base._UNTOUCHED
_starting = _base._starting
deploy = _base.deploy

#: The fixture pins TCKDB_DB_NETWORK; an empty value lets the script resolve it.
_RESOLVE = {"TCKDB_DB_NETWORK": ""}


def _backup(calls: list[str]) -> str:
    [call] = [call for call in calls if call.startswith("exec ")]
    return call


def _networks_used(calls: list[str]) -> set[str]:
    used = set()
    for call in calls:
        if call.startswith(("run ", "create ")):
            words = call.split()
            used.update(words[i + 1] for i, word in enumerate(words) if word == "--network")
    return used


def test_nothing_new_set_is_unchanged(deploy) -> None:
    result, calls = deploy(**_RESOLVE)
    assert result.returncode == 0, result.stderr
    assert _backup(calls) == "exec tckdbv2-db-1 pg_dump -U tckdb -d tckdb -Fc"
    assert _networks_used(calls) == {"tckdbv2_default"}


def test_backup_role_and_database_come_from_the_env_files(deploy, tmp_path: Path) -> None:
    (tmp_path / "env").write_text("DB_NAME=tckdb_lab\nDB_PASSWORD=x\n")
    migration = tmp_path / "db-admin.env"
    migration.write_text("DB_ADMIN_USER=tckdb_admin\nDB_OWNER_USER=o\nDB_OWNER_PASSWORD=p\n")
    result, calls = deploy(TCKDB_MIGRATION_ENV_FILE=str(migration))
    assert result.returncode == 0, result.stderr
    assert _backup(calls) == "exec tckdbv2-db-1 pg_dump -U tckdb_admin -d tckdb_lab -Fc"


def test_admin_role_in_the_api_file_is_used_when_no_migration_file_names_one(deploy, tmp_path: Path) -> None:
    (tmp_path / "env").write_text("DB_ADMIN_USER=\"pgadmin\"\nDB_PASSWORD=x\n")
    result, calls = deploy()
    assert result.returncode == 0, result.stderr
    assert _backup(calls) == "exec tckdbv2-db-1 pg_dump -U pgadmin -d tckdb -Fc"


def test_a_documented_stack_backs_up_as_the_containers_admin(deploy, tmp_path: Path) -> None:
    """The admin is in neither file: the migration file is owner-only, the API file runtime-only."""
    (tmp_path / "env").write_text("DB_NAME=tckdb\nDB_USER=tckdb_app\nDB_PASSWORD=x\n")
    owner = tmp_path / "owner.env"
    owner.write_text("DB_OWNER_USER=tckdb_owner\nDB_OWNER_PASSWORD=p\n")
    result, calls = deploy(
        TCKDB_MIGRATION_ENV_FILE=str(owner),
        FAKE_CONTAINER_ENV="PATH=/usr/bin POSTGRES_USER=tckdb_admin POSTGRES_DB=tckdb",
    )
    assert result.returncode == 0, result.stderr
    assert _backup(calls) == "exec tckdbv2-db-1 pg_dump -U tckdb_admin -d tckdb -Fc"
    assert "inspect --type container tckdbv2-db-1 --format {{range .Config.Env}}{{println .}}{{end}}" in calls


def test_the_database_name_falls_back_to_the_containers(deploy, tmp_path: Path) -> None:
    result, calls = deploy(FAKE_CONTAINER_ENV="POSTGRES_USER=pg POSTGRES_DB=labdb")
    assert result.returncode == 0, result.stderr
    assert _backup(calls) == "exec tckdbv2-db-1 pg_dump -U pg -d labdb -Fc"


def test_a_container_without_postgres_env_falls_back_to_tckdb(deploy) -> None:
    result, calls = deploy(FAKE_CONTAINER_ENV="")
    assert result.returncode == 0, result.stderr
    assert _backup(calls) == "exec tckdbv2-db-1 pg_dump -U tckdb -d tckdb -Fc"


def test_env_files_win_over_the_container(deploy, tmp_path: Path) -> None:
    (tmp_path / "env").write_text("DB_NAME=from_file\nDB_ADMIN_USER=file_admin\nDB_PASSWORD=x\n")
    result, calls = deploy(FAKE_CONTAINER_ENV="POSTGRES_USER=pg POSTGRES_DB=labdb")
    assert result.returncode == 0, result.stderr
    assert _backup(calls) == "exec tckdbv2-db-1 pg_dump -U file_admin -d from_file -Fc"


def test_compose_project_name_selects_its_db_and_network(deploy) -> None:
    result, calls = deploy(COMPOSE_PROJECT_NAME="labdb", FAKE_PS_DB="c0ffee", **_RESOLVE)
    assert result.returncode == 0, result.stderr
    assert _backup(calls).startswith("exec c0ffee pg_dump ")
    assert _networks_used(calls) == {"labdb_default"}
    assert "ps -q --filter label=com.docker.compose.project=labdb --filter label=com.docker.compose.service=db" in calls


def test_a_project_without_a_db_changes_nothing(deploy) -> None:
    result, calls = deploy(COMPOSE_PROJECT_NAME="labdb", **_RESOLVE)
    assert result.returncode != 0
    assert "no running db container in compose project 'labdb'" in result.stderr
    assert not _starting(calls, *_UNTOUCHED)


def test_without_the_default_container_compose_ps_finds_the_db(deploy) -> None:
    result, calls = deploy(
        MISSING_CONTAINERS="tckdbv2-db-1", FAKE_COMPOSE_DB="beef01", FAKE_PROJECT="tckdb", **_RESOLVE
    )
    assert result.returncode == 0, result.stderr
    assert _backup(calls).startswith("exec beef01 pg_dump ")
    assert _networks_used(calls) == {"tckdb_default"}


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit-wins", "default"])
def test_explicit_container_and_network_win(deploy, explicit) -> None:
    extra = {"TCKDB_DB_CONTAINER": "mydb", "TCKDB_DB_NETWORK": "mynet"} if explicit else _RESOLVE
    result, calls = deploy(COMPOSE_PROJECT_NAME="labdb", FAKE_PS_DB="c0ffee", **extra)
    assert result.returncode == 0, result.stderr
    if explicit:
        assert _backup(calls).startswith("exec mydb pg_dump ")
        assert _networks_used(calls) == {"mynet"}
    else:
        assert _backup(calls).startswith("exec c0ffee pg_dump ")


@pytest.mark.parametrize(
    ("systemctl", "expected"),
    [
        ('echo inactive; exit 3', "systemd uvicorn: inactive"),
        ('echo active; exit 0', "systemd uvicorn: active"),
        ("exit 1", "systemd uvicorn: n/a"),
    ],
    ids=["inactive", "active", "absent"],
)
def test_check_prints_one_systemd_state(tmp_path: Path, systemctl: str, expected: str) -> None:
    lines = _check(tmp_path, systemctl)
    systemd = [line for line in lines if line.startswith("systemd")]
    assert systemd == [expected], lines
    assert "n/a" not in lines


def test_check_prints_one_line_for_a_missing_container(tmp_path: Path) -> None:
    lines = _check(tmp_path, "exit 1", MISSING_CONTAINERS="tckdb-api")
    assert "container:      (not running as a container)" in lines, lines
    assert "networks:       n/a" in lines, lines
    assert "(not running as a container)" not in lines
    assert "n/a" not in lines


def _check(tmp_path: Path, systemctl: str, **extra: str) -> list[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (
        ("docker", _base._FAKE_DOCKER),
        ("curl", _base._FAKE_CURL),
        ("systemctl", f"#!/usr/bin/env bash\n{systemctl}\n"),
    ):
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    result = subprocess.run(
        ["bash", str(_base.SCRIPT), "--check"],
        env={
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "DEPLOY_LOG": str(tmp_path / "docker.log"),
            "DEPLOY_STATE": str(tmp_path / "state"),
            **extra,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()
