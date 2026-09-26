"""The SeaweedFS lockdown in ``docker-compose.yml`` stays locked down (#545).

Two things are pinned here, both of which a well-meaning edit could undo
without any other test noticing:

* **Network isolation.** ``seaweedfs`` is on the ``storage`` network and no
  other. Its gRPC ports take no credentials, and from another container on
  the same network ``weed shell`` deleted an object and the bucket's whole
  collection (measured on 4.47). Adding ``default`` back -- the obvious fix
  for "the API can't reach the store" -- would reopen that to ``db`` and to
  anything else in the project, so it fails here instead.
* **The key is checked at start, not at interpolation.** A ``${VAR:?}``
  guard made a MinIO-only stack fail to parse; the replacement must still
  refuse to start SeaweedFS without a key rather than fall back to
  well-known keys. The wrapper script is taken from the compose file and
  *executed*, with the image's ``/entrypoint.sh`` swapped for a stand-in.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def _networks(service: str) -> list[str]:
    networks = SERVICES[service].get("networks")
    if networks is None:
        return ["default"]
    return list(networks)


def test_seaweedfs_is_on_the_storage_network_only() -> None:
    assert _networks("seaweedfs") == ["storage"]
    assert "storage" in COMPOSE["networks"]


def test_only_store_clients_join_the_storage_network() -> None:
    on_storage = {name for name in SERVICES if "storage" in _networks(name)}
    assert on_storage == {"seaweedfs", "minio", "worker"}, on_storage
    assert "storage" not in _networks("db")


def test_seaweedfs_publishes_only_the_s3_gateway_on_loopback() -> None:
    assert SERVICES["seaweedfs"]["ports"] == ["127.0.0.1:9000:9000"]


def test_the_key_is_not_a_compose_interpolation_requirement() -> None:
    env = SERVICES["seaweedfs"]["environment"]
    assert env["SEAWEEDFS_JWT_KEY"] == "${SEAWEEDFS_JWT_KEY:-}"
    # Derived inside the container, never written out with a fixed suffix
    # where an empty key would leave only the suffix.
    assert not [key for key in env if key.startswith("WEED_JWT_")], env


# ---------------------------------------------------------------------------
# The wrapper, executed
# ---------------------------------------------------------------------------


def _wrapper_script() -> str:
    entrypoint = SERVICES["seaweedfs"]["entrypoint"]
    assert entrypoint[:2] == ["/bin/sh", "-c"], entrypoint
    # Compose turns ``$$`` into ``$`` before the container sees it.
    return entrypoint[2].replace("$$", "$")


@pytest.fixture
def run_wrapper(tmp_path: Path):
    fake = tmp_path / "entrypoint.sh"
    fake.write_text(
        "#!/bin/sh\n"
        'env | grep "^WEED_JWT_" | sort\n'
        'echo "ARGS $*"\n'
    )
    fake.chmod(0o755)
    script = _wrapper_script()
    assert "exec /entrypoint.sh " in script
    script = script.replace("exec /entrypoint.sh ", f"exec {fake} ")

    def _run(key: str | None) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ["PATH"]}
        if key is not None:
            env["SEAWEEDFS_JWT_KEY"] = key
        return subprocess.run(
            ["/bin/sh", "-c", script, "seaweedfs", "mini", "-dir=/data"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    return _run


@pytest.mark.parametrize("key", [None, "", "short-key"], ids=["unset", "empty", "15-chars"])
def test_without_a_usable_key_the_store_refuses_to_start(run_wrapper, key) -> None:
    result = run_wrapper(key)
    assert result.returncode == 78, result
    assert "refusing to start an unauthenticated store" in result.stderr
    assert "WEED_JWT_" not in result.stdout


def test_with_a_key_the_four_role_keys_are_derived_and_the_command_runs(run_wrapper) -> None:
    key = "0123456789abcdef0123"
    result = run_wrapper(key)
    assert result.returncode == 0, result
    assert result.stdout.splitlines() == [
        f"WEED_JWT_FILER_SIGNING_KEY={key}.filer-write",
        f"WEED_JWT_FILER_SIGNING_READ_KEY={key}.filer-read",
        f"WEED_JWT_SIGNING_KEY={key}.volume-write",
        f"WEED_JWT_SIGNING_READ_KEY={key}.volume-read",
        "ARGS mini -dir=/data",
    ]
