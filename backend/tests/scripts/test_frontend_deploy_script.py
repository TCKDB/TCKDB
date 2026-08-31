"""Executable rollback tests for the Pi-safe frontend deployment shell script."""

from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "frontend/scripts/ops/tckdb_frontend_deploy.sh"
TAG = "sha-0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def fake_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "commands.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(
        """#!/usr/bin/env bash
set -eu
echo "$*" >> "$FRONTEND_DEPLOY_LOG"
case "$1" in
  network|pull|logs) exit 0 ;;
  stop)
    if [[ "${FRONTEND_DEPLOY_MODE:-}" == signal_stop ]]; then
      kill -TERM "$PPID"
    fi
    exit 0 ;;
  start)
    if [[ "${FRONTEND_DEPLOY_MODE:-}" == *restore_start_fail* ]]; then
      exit 1
    fi
    exit 0 ;;
  rm)
    if [[ "${FRONTEND_DEPLOY_MODE:-}" == cleanup_fail && "${2:-}" != -f ]]; then
      exit 1
    fi
    exit 0 ;;
  rename)
    if [[ "${FRONTEND_DEPLOY_MODE:-}" == signal_rename && "$2" == tckdb-frontend ]]; then
      kill -TERM "$PPID"
    fi
    if [[ "${FRONTEND_DEPLOY_MODE:-}" == rename_fail ]] \
      || [[ "${FRONTEND_DEPLOY_MODE:-}" == *restore_rename_fail* && "$2" == tckdb-frontend-previous-* ]]; then
      exit 1
    fi
    exit 0 ;;
  run)
    if [[ "${FRONTEND_DEPLOY_MODE:-}" == signal_run || "${FRONTEND_DEPLOY_MODE:-}" == signal_first ]]; then
      kill -TERM "$PPID"
      exit 0
    fi
    [[ "${FRONTEND_DEPLOY_MODE:-}" != start_fail ]] && exit 0 || exit 1 ;;
  image) echo "laxzal/tckdb-frontend@sha256:old"; exit 0 ;;
  inspect)
    args="$*"
    if [[ "$args" == *"tckdb-frontend-previous-"* ]]; then
      if [[ "${FRONTEND_DEPLOY_MODE:-}" == occupied_backup ]]; then
        [[ "$args" == *".Id"* ]] && echo stale-backup-id
        exit 0
      fi
      if grep -q '^rename tckdb-frontend ' "$FRONTEND_DEPLOY_LOG" \
        && [[ "${FRONTEND_DEPLOY_MODE:-}" != rename_fail && "${FRONTEND_DEPLOY_MODE:-}" != signal_stop ]]; then
        [[ "$args" == *".Id"* ]] && echo fake-incumbent-id
        exit 0
      fi
      exit 1
    elif [[ "$args" == "inspect tckdb-frontend" && "${FRONTEND_DEPLOY_HAS_PREVIOUS:-true}" == false ]]; then
      exit 1
    elif [[ "$args" == *"tckdb-frontend"* && "$args" == *".Id"* ]]; then
      echo fake-incumbent-id
    elif [[ "$args" == *"tckdb-api"* && "$args" == *"State.Running"* ]]; then
      echo "${FRONTEND_DEPLOY_API_RUNNING:-true}"
    elif [[ "$args" == *"tckdb-api"* && "$args" == *"NetworkSettings.Networks"* ]]; then
      echo tckdbv2_default
    elif [[ "$args" == *"tckdb-frontend"* && "$args" == *"Config.Image"* ]]; then
      echo laxzal/tckdb-frontend:sha-old
    elif [[ "$args" == *"RepoDigests"* ]]; then
      echo laxzal/tckdb-frontend@sha256:new
    fi
    exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    (bindir / "curl").write_text(
        """#!/usr/bin/env bash
case "$*" in
  *api/v1/status*)
    if [[ "${FRONTEND_DEPLOY_MODE:-}" == *health_fail* ]]; then
      echo '{"status":"ok","degraded":["database"]}'
    elif [[ "${FRONTEND_DEPLOY_MODE:-}" == health_missing_degraded ]]; then
      echo '{"status":"ok"}'
    elif [[ "${FRONTEND_DEPLOY_MODE:-}" == health_wrong_degraded_type ]]; then
      echo '{"status":"ok","degraded":""}'
    else
      echo '{"status":"ok","degraded":[]}'
    fi ;;
  *) echo '<div id="root">' ;;
esac
""",
        encoding="utf-8",
    )
    (bindir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for command in bindir.iterdir():
        command.chmod(0o755)
    monkeypatch.setenv("FRONTEND_DEPLOY_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    # Scope the deploy script's advisory lock to this test's own tmp_path.
    # The script defaults it to a FIXED /tmp path (see the script's
    # LOCK_FILE line), which is right in production -- it is what stops two
    # real deployments mutating the same container. Under pytest-xdist that
    # single path is shared by every worker, so whichever test invokes the
    # script first holds it and the rest die on "another frontend
    # deployment holds ...; refusing concurrent mutation" -- a confident,
    # plausible error with nothing to do with the change under test.
    #
    # Each worker already gets an isolated tmp_path; the lock was the one
    # thing escaping that isolation, because the script resolves it
    # internally rather than receiving it. Setting it here covers every
    # test by default. `test_concurrent_lock_refuses_before_pull_or_mutation`
    # still passes its own path explicitly via `_run(extra_env=...)`, since
    # it has to hold the lock itself to assert the refusal.
    monkeypatch.setenv("TCKDB_FRONTEND_LOCK_FILE", str(tmp_path / "frontend-deploy.lock"))
    return log


def _run(mode: str = "", extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"FRONTEND_DEPLOY_MODE": mode}
    if extra_env:
        env.update(extra_env)
    return subprocess.run([str(SCRIPT), TAG], text=True, capture_output=True, env=env, check=False)


def test_stopped_api_refuses_before_pulling_or_swapping(fake_commands: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FRONTEND_DEPLOY_API_RUNNING", "false")
    result = _run()
    assert result.returncode == 1
    assert "is stopped" in result.stderr
    commands = fake_commands.read_text(encoding="utf-8")
    assert "pull" not in commands
    assert "stop tckdb-frontend" not in commands


@pytest.mark.parametrize(
    "mode",
    ["start_fail", "health_fail", "health_missing_degraded", "health_wrong_degraded_type"],
)
def test_failed_candidate_automatically_restores_preserved_container(fake_commands: Path, mode: str):
    result = _run(mode)
    assert result.returncode == 1
    commands = fake_commands.read_text(encoding="utf-8")
    assert "stop tckdb-frontend" in commands
    assert "rename tckdb-frontend tckdb-frontend-previous-" in commands
    assert "rm -f tckdb-frontend" in commands
    assert "rename tckdb-frontend-previous-" in commands
    assert "start tckdb-frontend" in commands
    assert "restored: laxzal/tckdb-frontend:sha-old" in result.stderr


def test_successful_first_install_needs_no_previous_frontend(fake_commands: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FRONTEND_DEPLOY_HAS_PREVIOUS", "false")
    result = _run()
    assert result.returncode == 0
    assert "first install" in result.stdout
    commands = fake_commands.read_text(encoding="utf-8")
    assert "run -d --name tckdb-frontend" in commands
    assert "stop tckdb-frontend" not in commands
    assert "rename tckdb-frontend" not in commands


@pytest.mark.parametrize(
    "mode",
    ["start_fail", "health_fail", "health_missing_degraded", "health_wrong_degraded_type"],
)
def test_failed_first_install_removes_candidate_without_bogus_restore(
    fake_commands: Path, monkeypatch: pytest.MonkeyPatch, mode: str
):
    monkeypatch.setenv("FRONTEND_DEPLOY_HAS_PREVIOUS", "false")
    result = _run(mode)
    assert result.returncode == 1
    assert "NPM remains on the API upstream" in result.stderr
    commands = fake_commands.read_text(encoding="utf-8")
    assert "rm -f tckdb-frontend" in commands
    assert "rename tckdb-frontend-previous" not in commands
    assert "start tckdb-frontend" not in commands


def test_rename_failure_restarts_the_stopped_frontend(fake_commands: Path):
    result = _run("rename_fail")
    assert result.returncode == 1
    commands = fake_commands.read_text(encoding="utf-8")
    assert "stop tckdb-frontend" in commands
    assert "rename tckdb-frontend tckdb-frontend-previous-" in commands
    assert "start tckdb-frontend" in commands
    assert "run -d --name tckdb-frontend" not in commands


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("signal_stop", "restarted incumbent container"),
        ("signal_rename", "restored: laxzal/tckdb-frontend:sha-old"),
        ("signal_run", "restored: laxzal/tckdb-frontend:sha-old"),
    ],
)
def test_signal_during_upgrade_restores_the_original_container(
    fake_commands: Path, mode: str, expected: str
):
    result = _run(mode)
    assert result.returncode == 1
    assert "received interrupt or termination signal" in result.stderr
    assert expected in result.stderr
    commands = fake_commands.read_text(encoding="utf-8")
    assert "start tckdb-frontend" in commands


def test_signal_during_first_install_removes_candidate_without_baseline(
    fake_commands: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FRONTEND_DEPLOY_HAS_PREVIOUS", "false")
    result = _run("signal_first")
    assert result.returncode == 1
    assert "first-install candidate removed" in result.stderr
    commands = fake_commands.read_text(encoding="utf-8")
    assert "rm -f tckdb-frontend" in commands
    assert "rename tckdb-frontend" not in commands


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("health_fail_restore_rename_fail", "manual recovery: docker rename"),
        ("health_fail_restore_start_fail", "manual recovery: docker start"),
    ],
)
def test_restore_failures_are_loud_and_do_not_recurse(fake_commands: Path, mode: str, expected: str):
    result = _run(mode)
    assert result.returncode == 1
    assert expected in result.stderr
    assert result.stderr.count("restoring the previous frontend") == 1


def test_backup_cleanup_failure_warns_but_keeps_successful_deployment(fake_commands: Path):
    result = _run("cleanup_fail")
    assert result.returncode == 0
    assert "warning: deployed, but preserved backup remains" in result.stderr
    assert "==> deployed" in result.stdout


def test_occupied_preservation_name_refuses_before_stopping_or_replacing(fake_commands: Path):
    result = _run("occupied_backup")
    assert result.returncode == 1
    assert "preservation name tckdb-frontend-previous-fake-incumbe is already occupied" in result.stderr
    commands = fake_commands.read_text(encoding="utf-8")
    assert "stop tckdb-frontend" not in commands
    assert "rm -f tckdb-frontend" not in commands
    assert "run -d --name tckdb-frontend" not in commands
    assert "inspect tckdb-frontend --format {{.Id}}" in commands


def test_concurrent_lock_refuses_before_pull_or_mutation(fake_commands: Path, tmp_path: Path):
    lock_path = tmp_path / "frontend-deploy.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run(extra_env={"TCKDB_FRONTEND_LOCK_FILE": str(lock_path)})
    assert result.returncode == 1
    assert "another frontend deployment holds" in result.stderr
    assert not fake_commands.exists()
