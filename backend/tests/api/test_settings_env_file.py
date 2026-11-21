"""``Settings`` reads ``backend/.env`` when it exists, and the environment wins over it."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import app
from app.api.config import ENV_FILE, Settings

BACKEND_DIR = Path(app.__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``Settings`` at a file under ``tmp_path`` and clear the keys the tests set."""
    path = tmp_path / ".env"
    monkeypatch.setitem(Settings.model_config, "env_file", path)
    for key in ("DB_NAME", "DB_PORT", "DEPLOYMENT_MODE"):
        monkeypatch.delenv(key, raising=False)
    return path


def test_the_file_is_backend_dot_env_whatever_the_working_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    assert ENV_FILE == BACKEND_DIR / ".env"
    assert ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == ENV_FILE


def test_values_in_the_file_are_read(env_file: Path) -> None:
    env_file.write_text("DB_NAME=from_file\nDB_PORT=6543\nDEPLOYMENT_MODE=shared_private\n")
    s = Settings()
    assert (s.db_name, s.db_port, s.deployment_mode) == ("from_file", 6543, "shared_private")


def test_an_environment_variable_wins_over_the_file(env_file: Path, monkeypatch) -> None:
    env_file.write_text("DB_NAME=from_file\nDB_PORT=6543\n")
    monkeypatch.setenv("DB_NAME", "from_env")
    s = Settings()
    assert s.db_name == "from_env"
    assert s.db_port == 6543


def test_a_missing_file_leaves_the_defaults(env_file: Path) -> None:
    assert not env_file.exists()
    s = Settings()
    assert (s.db_name, s.db_port, s.deployment_mode) == ("tckdb_dev", 5432, "local")


def test_keys_meant_for_other_tools_are_ignored(env_file: Path) -> None:
    env_file.write_text("TCKDB_API_HOST=127.0.0.1\nTCKDB_INLINE_WORKER=true\nDB_NAME=from_file\n")
    assert Settings().db_name == "from_file"


def test_the_api_image_never_contains_a_dot_env() -> None:
    """The image reads ``/app/backend/.env`` if one is there, so the build context must exclude it."""
    lines = [line.strip() for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()]
    assert "**/.env" in lines
    assert "**/.env.*" in lines
    dockerfile = (BACKEND_DIR / "Dockerfile").read_text()
    assert ".env" not in "\n".join(line for line in dockerfile.splitlines() if line.startswith(("COPY", "ADD")))


_IMPORT_MAIN = """
import sys
from pathlib import Path

import app.api.config as config

config.ENV_FILE = Path(sys.argv[1])
import main  # noqa: E402,F401
from app.services import artifact_storage  # noqa: E402

print(artifact_storage.S3_BUCKET, artifact_storage.S3_REGION)
"""


def test_the_api_entry_point_exports_the_file_to_modules_that_read_the_environment(tmp_path: Path) -> None:
    """``uvicorn main:app`` sees ``S3_*`` from the file, and the environment still wins."""
    env_file = tmp_path / ".env"
    env_file.write_text("S3_BUCKET=bucket-from-file\nS3_REGION=region-from-file\n")
    env = {k: v for k, v in os.environ.items() if k not in {"S3_BUCKET", "S3_REGION", "TCKDB_INLINE_WORKER"}}
    env |= {"S3_REGION": "region-from-env", "TCKDB_STARTUP_PROBES": "false"}
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_MAIN, str(env_file)],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-2:] == ["bucket-from-file", "region-from-env"]
