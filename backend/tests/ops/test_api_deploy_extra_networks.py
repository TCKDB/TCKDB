"""``tckdb_deploy.sh`` joins the API to the networks it is told to, and no others.

The compose file keeps SeaweedFS on its own ``storage`` network (#545), so a
containerised API that uses it must join that network too.
``TCKDB_EXTRA_NETWORKS`` is how: unset changes nothing, set connects the new
container after it starts, and a network that does not exist stops the deploy
before the running API is touched. ``docker`` and ``curl`` are fakes that log
their arguments, so these tests run anywhere and change nothing.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "backend/scripts/ops/tckdb_deploy.sh"
TAG = "sha-0123456789abcdef0123456789abcdef01234567"

_FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$DEPLOY_LOG"
case "$1" in
  network)
    if [[ "$2" == inspect && " ${MISSING_NETWORKS:-} " == *" $3 "* ]]; then exit 1; fi
    exit 0 ;;
  inspect) echo "laxzal/tckdb-api@sha256:fake"; exit 0 ;;
  exec) echo "fake dump"; exit 0 ;;
  *) exit 0 ;;
esac
"""

_FAKE_CURL = """#!/usr/bin/env bash
echo '{"status":"ok","degraded":[],"components":{}}'
"""


@pytest.fixture
def deploy(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("docker", _FAKE_DOCKER), ("curl", _FAKE_CURL)):
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    log = tmp_path / "docker.log"
    env_file = tmp_path / "env"
    env_file.write_text("DB_PASSWORD=x\n")

    def _run(**extra: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        env = {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "DEPLOY_LOG": str(log),
            "TCKDB_ENV_FILE": str(env_file),
            "TCKDB_BACKUP_DIR": str(tmp_path / "backups"),
            **extra,
        }
        result = subprocess.run(
            ["bash", str(SCRIPT), TAG], env=env, capture_output=True, text=True, timeout=60
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls

    return _run


def _index(calls: list[str], prefix: str) -> int:
    return next(i for i, call in enumerate(calls) if call.startswith(prefix))


def test_unset_joins_no_extra_network(deploy) -> None:
    result, calls = deploy()
    assert result.returncode == 0, result.stderr
    assert any(call.startswith("run -d --name tckdb-api") for call in calls), calls
    assert not [call for call in calls if call.startswith("network connect")], calls


def test_extra_networks_are_joined_after_the_container_starts(deploy) -> None:
    result, calls = deploy(TCKDB_EXTRA_NETWORKS="tckdbv2_storage, other_net")
    assert result.returncode == 0, result.stderr
    started = _index(calls, "run -d --name tckdb-api")
    assert calls[started + 1] == "network connect tckdbv2_storage tckdb-api", calls
    assert calls[started + 2] == "network connect other_net tckdb-api", calls
    assert "joined network: tckdbv2_storage" in result.stdout


def test_a_missing_network_stops_before_the_running_api_is_touched(deploy) -> None:
    result, calls = deploy(
        TCKDB_EXTRA_NETWORKS="tckdbv2_storage", MISSING_NETWORKS="tckdbv2_storage"
    )
    assert result.returncode != 0
    assert "tckdbv2_storage' does not exist" in result.stderr
    for forbidden in ("rm -f tckdb-api", "run ", "exec ", "pull "):
        assert not [call for call in calls if call.startswith(forbidden)], (forbidden, calls)
