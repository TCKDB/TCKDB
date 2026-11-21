"""``check_selfhosted_deployment.sh`` checks secrets only where the API requires them.

The API refuses empty or placeholder secrets in ``shared_private`` and ``hosted_public`` and skips the check in
``local`` (``app.api.startup_checks.validate_deployment_safety``); ``DEPLOYMENT_MODE`` unset means ``local``. The
script follows the same rule, so a developer's ``.env`` (no ``PAGING_CURSOR_KEY``) passes.

``docker`` and ``curl`` are fakes that report a healthy stack, so only the secrets section can fail.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "backend" / "scripts" / "check_selfhosted_deployment.sh"

_FAKE_DOCKER = r"""#!/usr/bin/env bash
case " $* " in
    *" --version "*) echo "Docker version 0.0.0-fake" ;;
    *" ps --format json "*)
        echo '{"Service":"db","State":"running","Health":"healthy"}'
        echo '{"Service":"seaweedfs","State":"running","Health":"healthy"}' ;;
    *" ps -q tckdb-api "*) echo "0123456789ab" ;;
    *" psql "*) echo "rdkit" ;;
    *"/alembic current "*) echo "d5b8e2f4a916 (head)" ;;
    *) exit 1 ;;
esac
"""

_FAKE_CURL = r"""#!/usr/bin/env bash
case " $* " in
    *" -w "*) printf 200 ;;
    *) echo '{"status":"ok"}' ;;
esac
"""

_HOSTED_SECRETS = {
    "DB_PASSWORD": "db-secret-value",
    "S3_ACCESS_KEY": "s3-access-value",
    "S3_SECRET_KEY": "s3-secret-value",
    "SEAWEEDFS_JWT_KEY": "jwt-secret-value",
    "PAGING_CURSOR_KEY": "p" * 40,
}


@pytest.fixture
def check(tmp_path: Path):
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"docker": _FAKE_DOCKER, "curl": _FAKE_CURL}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")

    def run(env_lines: dict[str, str], admin: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        (tmp_path / "check.env").write_text("".join(f"{k}={v}\n" for k, v in env_lines.items()))
        if admin is not None:
            (tmp_path / "check.db-admin.env").write_text("".join(f"{k}={v}\n" for k, v in admin.items()))
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "COMPOSE_ENV_FILE": "check.env",
            "COMPOSE_ADMIN_ENV_FILE": "check.db-admin.env",
            "TCKDB_BASE_URL": "http://127.0.0.1:9/api/v1",
        }
        return subprocess.run(
            ["bash", str(SCRIPT)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=False
        )

    return run


def test_the_developer_env_passes_without_a_paging_cursor_key(check) -> None:
    developer_env = (REPO_ROOT / ".env.example").read_text()
    keys = dict(line.split("=", 1) for line in developer_env.splitlines() if line and not line.startswith("#"))
    assert "PAGING_CURSOR_KEY" not in keys and "DEPLOYMENT_MODE" not in keys
    result = check(keys)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PAGING_CURSOR_KEY" not in result.stderr
    assert "secrets not checked: DEPLOYMENT_MODE is local" in result.stdout


@pytest.mark.parametrize("mode", ["local", "'local'"])
def test_local_mode_skips_runtime_and_operator_secrets(check, mode: str) -> None:
    result = check(
        {"DEPLOYMENT_MODE": mode, "DB_PASSWORD": "change-me"},
        admin={"DB_ADMIN_PASSWORD": "change-me", "DB_OWNER_PASSWORD": ""},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout + result.stderr
    assert "secrets not checked: DEPLOYMENT_MODE is local" in result.stdout


@pytest.mark.parametrize("mode", ["shared_private", "hosted_public"])
def test_a_hosted_mode_still_fails_a_missing_paging_cursor_key(check, mode: str) -> None:
    secrets = {k: v for k, v in _HOSTED_SECRETS.items() if k != "PAGING_CURSOR_KEY"}
    result = check({"DEPLOYMENT_MODE": mode, **secrets}, admin={"DB_ADMIN_PASSWORD": "a1", "DB_OWNER_PASSWORD": "o1"})
    assert result.returncode == 1
    assert "PAGING_CURSOR_KEY is empty or unset in check.env" in result.stderr
    assert result.stderr.count("FAIL") == 1, result.stderr


def test_a_hosted_mode_still_fails_an_operator_placeholder(check) -> None:
    result = check(
        {"DEPLOYMENT_MODE": "shared_private", **_HOSTED_SECRETS},
        admin={"DB_ADMIN_PASSWORD": "change-me-admin", "DB_OWNER_PASSWORD": "o1"},
    )
    assert result.returncode == 1
    assert "DB_ADMIN_PASSWORD is still a change-me placeholder" in result.stderr


def test_a_complete_hosted_env_passes(check) -> None:
    result = check(
        {"DEPLOYMENT_MODE": "shared_private", **_HOSTED_SECRETS},
        admin={"DB_ADMIN_PASSWORD": "a1", "DB_OWNER_PASSWORD": "o1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PAGING_CURSOR_KEY is set" in result.stdout
    assert "DEPLOYMENT_MODE=shared_private" in result.stdout


def test_the_harness_reaches_every_section(check) -> None:
    """Guards the fakes: a passing run must have gone through compose, psql, alembic and both requests."""
    result = check(
        {"DEPLOYMENT_MODE": "shared_private", **_HOSTED_SECRETS},
        admin={"DB_ADMIN_PASSWORD": "a", "DB_OWNER_PASSWORD": "o"},
    )
    for marker in ("db is running", "rdkit extension present", "(head)", "GET /health", "-> 200", "all checks passed"):
        assert marker in result.stdout, (marker, result.stdout)
