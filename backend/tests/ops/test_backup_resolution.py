"""``tckdb_backup.sh`` finds the database of whichever compose project it serves.

What is pinned here:

* with only ``TCKDB_BACKUP_DEST`` set on a host whose ``tckdbv2-db-1`` exists,
  every ``docker exec`` is exactly the sequence the script has always run
  (container ``tckdbv2-db-1``, role ``tckdb``, database ``tckdb``), and the
  container's own environment is not consulted;
* ``TCKDB_ENV_FILE`` naming ``COMPOSE_PROJECT_NAME`` (as a bundle's
  ``.env.selfhosted`` does) selects that project's ``db``, backs up as the
  container's admin role and the env file's ``DB_NAME``;
* ``COMPOSE_PROJECT_NAME`` in the environment wins over the env file;
  ``TCKDB_DB_CONTAINER``, ``TCKDB_DB_USER`` and ``TCKDB_DB_NAME`` win over both;
* without the legacy container, ``docker compose ps -q db`` (given the env
  file) finds the db;
* a project with no ``db`` stops before anything is dumped.

``docker`` is a fake that logs its arguments and answers ``pg_dump`` and
``psql`` the way a healthy UTF8 database does.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "backend/scripts/ops/tckdb_backup.sh"

_FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "$*" >> "$DOCKER_LOG"
case "$1" in
  inspect)
    args=("${@:2}"); target=""
    for ((i = 0; i < ${#args[@]}; i++)); do
      case "${args[i]}" in --type|--format) ((i++)) ;; *) target="${args[i]}"; break ;; esac
    done
    [[ " ${MISSING_CONTAINERS:-} " == *" $target "* ]] && exit 1
    if [[ "$*" == *Config.Env* ]]; then
      for kv in ${FAKE_CONTAINER_ENV-POSTGRES_USER=pgadmin POSTGRES_DB=pgdb}; do echo "$kv"; done
    fi
    exit 0 ;;
  ps) [[ -n "${FAKE_PS_DB:-}" ]] && echo "$FAKE_PS_DB"; exit 0 ;;
  compose) [[ -n "${FAKE_COMPOSE_DB:-}" ]] && echo "$FAKE_COMPOSE_DB"; exit 0 ;;
  exec)
    [[ "$2" == -i ]] && shift
    tool="$3"
    db=""; prev=""
    for a in "$@"; do [[ "$prev" == -d ]] && db="$a"; prev="$a"; done
    if [[ "$tool" == pg_dump ]]; then
      if [[ "$*" == *--create* ]]; then
        # Split so this fake dump does not read as a test that creates a database.
        echo "CREATE ""DATABASE $db WITH TEMPLATE = template0 ENCODING = 'UTF8';"
        echo "ALTER DATABASE $db OWNER TO x;"
        echo "\\connect $db"
      fi
      echo "CREATE TABLE t (x int);"
      exit 0
    fi
    if [[ "$tool" == psql ]]; then
      case "$*" in
        *pg_encoding_to_char*) echo UTF8 ;;
        *convert_from*) echo 1 ;;
        *) cat >/dev/null ;;
      esac
      exit 0
    fi
    exit 0 ;;
esac
exit 0
"""


@pytest.fixture
def backup(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "docker"
    fake.write_text(_FAKE_DOCKER)
    fake.chmod(0o755)
    log = tmp_path / "docker.log"
    dest = tmp_path / "backups"

    def _run(**extra: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        log.unlink(missing_ok=True)
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env={
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "HOME": str(tmp_path),
                "DOCKER_LOG": str(log),
                "TCKDB_BACKUP_DEST": str(dest),
                **extra,
            },
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        calls = log.read_text().splitlines() if log.exists() else []
        # The scratch database carries the script's PID.
        return result, [re.sub(r"tckdb_test_restore_check_\d+", "SCRATCH", c) for c in calls]

    return _run


def _execs(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("exec ")]


#: The docker exec sequence the script ran before it resolved anything, on
#: the host whose db is tckdbv2-db-1 and that sets only TCKDB_BACKUP_DEST.
LEGACY_EXECS = [
    "exec tckdbv2-db-1 pg_dump -U tckdb -d tckdb --create",
    "exec -i tckdbv2-db-1 psql -U tckdb -d postgres -v ON_ERROR_STOP=1 -tAq -c DROP DATABASE IF EXISTS \"SCRATCH\"",
    "exec -i tckdbv2-db-1 psql -U tckdb -d postgres -v ON_ERROR_STOP=1 -q",
    "exec -i tckdbv2-db-1 psql -U tckdb -d postgres -v ON_ERROR_STOP=1 -tAq -c SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname='SCRATCH'",
    "exec -i tckdbv2-db-1 psql -U tckdb -d SCRATCH -v ON_ERROR_STOP=1 -tAq -c SELECT length(convert_from('\\xe28094'::bytea, 'UTF8'))",
    "exec tckdbv2-db-1 pg_dump -U tckdb -d tckdb",
    "exec tckdbv2-db-1 pg_dump -U tckdb -d SCRATCH",
    "exec -i tckdbv2-db-1 psql -U tckdb -d postgres -v ON_ERROR_STOP=1 -tAq -c DROP DATABASE IF EXISTS \"SCRATCH\"",
    "exec -i tckdbv2-db-1 psql -U tckdb -d postgres -v ON_ERROR_STOP=1 -tAq -c DROP DATABASE IF EXISTS \"SCRATCH\"",
]


def test_only_backup_dest_set_runs_the_legacy_sequence(backup) -> None:
    result, calls = backup()
    assert result.returncode == 0, result.stderr
    assert _execs(calls) == LEGACY_EXECS
    # Read-only probes only besides the execs, and never the container's env.
    assert [c for c in calls if not c.startswith("exec ")] == [
        "inspect --type container tckdbv2-db-1 --format {{.Id}}"
    ]


def _write_env(tmp_path: Path, text: str) -> Path:
    env = tmp_path / ".env.selfhosted"
    env.write_text(text)
    return env


def test_a_bundle_env_file_selects_its_project(backup, tmp_path: Path) -> None:
    env = _write_env(tmp_path, "COMPOSE_PROJECT_NAME=tckdb\nDB_NAME=tckdb\nDB_USER=tckdb_app\n")
    result, calls = backup(TCKDB_ENV_FILE=str(env), FAKE_PS_DB="c0ffee", FAKE_CONTAINER_ENV="POSTGRES_USER=tckdb_admin")
    assert result.returncode == 0, result.stderr
    assert "ps -q --filter label=com.docker.compose.project=tckdb --filter label=com.docker.compose.service=db" in calls
    execs = _execs(calls)
    assert execs[0] == "exec c0ffee pg_dump -U tckdb_admin -d tckdb --create"
    assert all(" c0ffee " in c and "-U tckdb_admin " in c for c in execs), execs


def test_environment_project_wins_over_the_env_file(backup, tmp_path: Path) -> None:
    env = _write_env(tmp_path, "COMPOSE_PROJECT_NAME=tckdb\n")
    result, calls = backup(TCKDB_ENV_FILE=str(env), COMPOSE_PROJECT_NAME="lab", FAKE_PS_DB="c0ffee")
    assert result.returncode == 0, result.stderr
    assert "ps -q --filter label=com.docker.compose.project=lab --filter label=com.docker.compose.service=db" in calls
    assert _execs(calls)[0] == "exec c0ffee pg_dump -U pgadmin -d pgdb --create"


def test_explicit_overrides_win(backup, tmp_path: Path) -> None:
    env = _write_env(tmp_path, "COMPOSE_PROJECT_NAME=tckdb\nDB_NAME=fromfile\nDB_ADMIN_USER=fileadmin\n")
    result, calls = backup(
        TCKDB_ENV_FILE=str(env),
        TCKDB_DB_CONTAINER="mydb",
        TCKDB_DB_USER="me",
        TCKDB_DB_NAME="mine",
    )
    assert result.returncode == 0, result.stderr
    assert _execs(calls)[0] == "exec mydb pg_dump -U me -d mine --create"
    assert not any(c.startswith("ps ") for c in calls)


def test_env_file_admin_and_name_win_over_the_container(backup, tmp_path: Path) -> None:
    env = _write_env(tmp_path, "COMPOSE_PROJECT_NAME=tckdb\nDB_NAME=fromfile\nDB_ADMIN_USER=fileadmin\n")
    result, calls = backup(TCKDB_ENV_FILE=str(env), FAKE_PS_DB="c0ffee")
    assert result.returncode == 0, result.stderr
    assert _execs(calls)[0] == "exec c0ffee pg_dump -U fileadmin -d fromfile --create"


def test_without_the_legacy_container_compose_ps_finds_the_db(backup, tmp_path: Path) -> None:
    env = _write_env(tmp_path, "DB_NAME=tckdb\n")
    result, calls = backup(TCKDB_ENV_FILE=str(env), MISSING_CONTAINERS="tckdbv2-db-1", FAKE_COMPOSE_DB="beef01")
    assert result.returncode == 0, result.stderr
    assert f"compose --env-file {env} ps -q db" in calls
    assert _execs(calls)[0] == "exec beef01 pg_dump -U pgadmin -d tckdb --create"


def test_a_project_without_a_db_dumps_nothing(backup, tmp_path: Path) -> None:
    env = _write_env(tmp_path, "COMPOSE_PROJECT_NAME=tckdb\n")
    result, calls = backup(TCKDB_ENV_FILE=str(env))
    assert result.returncode == 1
    assert "no running db container in compose project 'tckdb'" in result.stderr
    assert _execs(calls) == []


def test_an_unreadable_env_file_is_refused(backup, tmp_path: Path) -> None:
    result, calls = backup(TCKDB_ENV_FILE=str(tmp_path / "missing.env"))
    assert result.returncode == 1
    assert "TCKDB_ENV_FILE" in result.stderr
    assert _execs(calls) == []
