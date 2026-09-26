"""``tckdb_deploy.sh`` joins the API to the networks it is told to, and no others.

The compose file keeps SeaweedFS on its own ``storage`` network (#545), so a
containerised API that uses it must join that network too.
``TCKDB_EXTRA_NETWORKS`` is how. What is pinned here:

* unset, the deploy is exactly what it was: ``rm -f`` then ``run -d``;
* set, the new container is *created*, connected, and only then started, so
  it never runs detached from the object store, and the running API is not
  removed until the new one is fully wired;
* a value that cannot work -- the DB network itself, a duplicate, ``host``,
  ``none``, a network that does not exist -- stops the deploy before the
  backup, the pull, the migration or the running API is touched;
* a connect that fails anyway removes what was created, leaves the running
  API alone, and says to fix the networks rather than to "roll back" (which
  would fail at the same step).

``docker`` and ``curl`` are fakes that log their arguments. The fake's
``network connect`` fails the way Docker 29 does on a running container
(measured): for a network the container is already on, and for ``host`` or
``none``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "backend/scripts/ops/tckdb_deploy.sh"
TAG = "sha-0123456789abcdef0123456789abcdef01234567"
DB_NETWORK = "tckdbv2_default"

_FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$DEPLOY_LOG"
state="$DEPLOY_STATE"
case "$1" in
  network)
    if [[ "$2" == inspect ]]; then
      [[ " ${MISSING_NETWORKS:-} " == *" $3 "* ]] && exit 1
      exit 0
    fi
    if [[ "$2" == connect ]]; then
      net="$3"; ctr="$4"
      if [[ "$net" == host || "$net" == none ]]; then
        echo "Error response from daemon: cannot connect container to $net" >&2; exit 1
      fi
      if grep -qx "$ctr $net" "$state" 2>/dev/null; then
        echo "Error response from daemon: endpoint with name $ctr already exists in network $net" >&2; exit 1
      fi
      [[ " ${FAIL_CONNECT:-} " == *" $net "* ]] && { echo "Error response from daemon: scripted" >&2; exit 1; }
      echo "$ctr $net" >> "$state"; exit 0
    fi
    exit 0 ;;
  run|create)
    # Record the container's first network: `--name <c> ... --network <n>`.
    args=("$@"); name=""; net=""
    for ((i = 0; i < ${#args[@]}; i++)); do
      [[ "${args[i]}" == --name ]] && name="${args[i+1]}"
      [[ "${args[i]}" == --network ]] && net="${args[i+1]}"
    done
    [[ -n "$name" ]] && echo "$name $net" >> "$state"
    [[ "$1" == create ]] && echo "fake-container-id"
    exit 0 ;;
  inspect) echo "laxzal/tckdb-api@sha256:fake"; exit 0 ;;
  exec) echo "fake dump"; exit 0 ;;
  *) exit 0 ;;
esac
"""

_FAKE_CURL = """#!/usr/bin/env bash
echo '{"status":"ok","degraded":[],"components":{}}'
"""

#: The swap as it has always been, for the unset case.
_LEGACY_SWAP = [
    "rm -f tckdb-api",
    f"run -d --name tckdb-api --network {DB_NETWORK} --env-file ",
]


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
            "DEPLOY_STATE": str(tmp_path / "networks.state"),
            "TCKDB_ENV_FILE": str(env_file),
            "TCKDB_BACKUP_DIR": str(tmp_path / "backups"),
            "TCKDB_DB_NETWORK": DB_NETWORK,
            **extra,
        }
        result = subprocess.run(
            ["bash", str(SCRIPT), TAG], env=env, capture_output=True, text=True, timeout=60
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls

    return _run


def _starting(calls: list[str], *prefixes: str) -> list[str]:
    return [call for call in calls if call.startswith(prefixes)]


# ---------------------------------------------------------------------------
# Unset: unchanged
# ---------------------------------------------------------------------------


def test_unset_swaps_exactly_as_before(deploy) -> None:
    result, calls = deploy()
    assert result.returncode == 0, result.stderr
    swap = _starting(calls, "rm -f tckdb-api", "run -d", "create", "start", "rename", "network connect")
    assert len(swap) == 2, swap
    assert swap[0] == _LEGACY_SWAP[0]
    assert swap[1].startswith(_LEGACY_SWAP[1]), swap[1]


# ---------------------------------------------------------------------------
# Set: created, connected, then started
# ---------------------------------------------------------------------------


def test_extra_networks_are_joined_before_the_container_starts(deploy) -> None:
    result, calls = deploy(TCKDB_EXTRA_NETWORKS="tckdbv2_storage, other_net")
    assert result.returncode == 0, result.stderr
    swap = _starting(calls, "rm -f tckdb-api", "run -d", "create", "start", "rename", "network connect")
    assert swap[0] == "rm -f tckdb-api-next", swap
    assert swap[1].startswith(f"create --name tckdb-api-next --network {DB_NETWORK} "), swap
    assert swap[2:] == [
        "network connect tckdbv2_storage tckdb-api-next",
        "network connect other_net tckdb-api-next",
        "rm -f tckdb-api",
        "rename tckdb-api-next tckdb-api",
        "start tckdb-api",
    ], swap
    assert "joined network: tckdbv2_storage" in result.stdout
    assert not _starting(calls, "run -d"), "the container was started before it was connected"


# ---------------------------------------------------------------------------
# Values that cannot work are refused before anything changes
# ---------------------------------------------------------------------------

_UNTOUCHED = ("exec ", "pull ", "run ", "create ", "rm ", "start ", "rename ", "network connect")


@pytest.mark.parametrize(
    ("value", "complaint"),
    [
        (f"{DB_NETWORK},tckdbv2_storage", "already the API's network"),
        ("tckdbv2_storage tckdbv2_storage", "more than once"),
        ("host", "cannot be joined"),
        ("none", "cannot be joined"),
    ],
    ids=["db-network", "duplicate", "host", "none"],
)
def test_a_value_that_cannot_work_is_refused_before_anything_changes(
    deploy, value, complaint
) -> None:
    result, calls = deploy(TCKDB_EXTRA_NETWORKS=value)
    assert result.returncode != 0
    assert complaint in result.stderr, result.stderr
    assert "nothing has been changed" in result.stderr, result.stderr
    assert not _starting(calls, *_UNTOUCHED), calls


def test_a_missing_network_is_refused_before_anything_changes(deploy) -> None:
    result, calls = deploy(
        TCKDB_EXTRA_NETWORKS="tckdbv2_storage", MISSING_NETWORKS="tckdbv2_storage"
    )
    assert result.returncode != 0
    assert "tckdbv2_storage' does not exist" in result.stderr
    assert not _starting(calls, *_UNTOUCHED), calls


# ---------------------------------------------------------------------------
# A connect that fails anyway
# ---------------------------------------------------------------------------


def test_a_failed_connect_leaves_the_running_api_alone(deploy) -> None:
    result, calls = deploy(
        TCKDB_EXTRA_NETWORKS="tckdbv2_storage", FAIL_CONNECT="tckdbv2_storage"
    )
    assert result.returncode != 0
    # What was created is gone again; the running API was never removed,
    # renamed over or restarted.
    assert "rm -f tckdb-api-next" in calls[calls.index("network connect tckdbv2_storage tckdb-api-next"):], calls
    assert "rm -f tckdb-api" not in calls, calls
    assert not _starting(calls, "start", "rename", "run -d"), calls
    # The advice has to be something that works: "roll back" would fail at
    # the same step after another backup and migration.
    assert "roll back" not in result.stderr, result.stderr
    assert "TCKDB_EXTRA_NETWORKS" in result.stderr
    assert "docker network connect" in result.stderr
    assert "still running" in result.stderr
