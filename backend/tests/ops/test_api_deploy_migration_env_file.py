"""``tckdb_deploy.sh`` gives migration-only credentials to Alembic and nothing else.

With separate owner and runtime roles (``docs/deployment/database_roles.md``),
Alembic needs ``DB_OWNER_USER`` / ``DB_OWNER_PASSWORD`` and the API must never
hold them. ``TCKDB_MIGRATION_ENV_FILE`` is where they go. What is pinned here:

* unset, the migration run is exactly what it was: one ``--env-file``;
* set, the ``alembic upgrade`` run reads it after ``TCKDB_ENV_FILE`` (so its
  values win), and no API container command ever names it;
* a path that is not a readable file stops the deploy before the backup, the
  pull, the migration or the running API is touched;
* owner or admin credentials left in ``TCKDB_ENV_FILE`` are reported.

The ``docker`` and ``curl`` fakes are the ones the extra-networks test uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.ops import test_api_deploy_extra_networks as _base

_UNTOUCHED = _base._UNTOUCHED
_starting = _base._starting
#: The same fake-``docker`` harness, re-exposed as this module's fixture.
deploy = _base.deploy


def _migration_runs(calls: list[str]) -> list[str]:
    return [call for call in calls if call.startswith("run --rm") and "alembic upgrade head" in call]


def _api_runs(calls: list[str]) -> list[str]:
    return [call for call in calls if call.startswith(("run -d", "create "))]


def test_unset_migrates_with_the_one_env_file(deploy, tmp_path: Path) -> None:
    result, calls = deploy()
    assert result.returncode == 0, result.stderr
    [migration] = _migration_runs(calls)
    assert migration.count("--env-file") == 1, migration


@pytest.mark.parametrize(
    "extra",
    [{}, {"TCKDB_EXTRA_NETWORKS": "tckdbv2_storage"}],
    ids=["run-d", "create-then-start"],
)
def test_set_reaches_alembic_after_the_api_file_and_never_the_api(
    deploy, tmp_path: Path, extra
) -> None:
    owner = tmp_path / "migration.env"
    owner.write_text("DB_OWNER_USER=o\nDB_OWNER_PASSWORD=p-SENTINEL\n")
    result, calls = deploy(TCKDB_MIGRATION_ENV_FILE=str(owner), **extra)
    assert result.returncode == 0, result.stderr
    [migration] = _migration_runs(calls)
    api_file = f"--env-file {tmp_path / 'env'}"
    owner_file = f"--env-file {owner}"
    assert api_file in migration and owner_file in migration, migration
    assert migration.index(api_file) < migration.index(owner_file), migration
    api_runs = _api_runs(calls)
    assert api_runs, calls
    assert all(str(owner) not in call for call in api_runs), api_runs
    assert "p-SENTINEL" not in result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_an_unreadable_path_is_refused_before_anything_changes(deploy, tmp_path: Path, kind) -> None:
    path = tmp_path / "nope.env"
    if kind == "directory":
        path.mkdir()
    result, calls = deploy(TCKDB_MIGRATION_ENV_FILE=str(path))
    assert result.returncode != 0
    assert "TCKDB_MIGRATION_ENV_FILE" in result.stderr, result.stderr
    assert "nothing has been changed" in result.stderr, result.stderr
    assert not _starting(calls, *_UNTOUCHED), calls


@pytest.mark.parametrize(
    "line",
    [
        "DB_OWNER_PASSWORD=p-SENTINEL",
        "  DB_OWNER_PASSWORD=p-SENTINEL",
        "export DB_ADMIN_PASSWORD=p-SENTINEL",
        # No `=`: Docker copies the value from the deploying shell.
        "DB_OWNER_PASSWORD",
    ],
    ids=["plain", "indented", "export", "bare-name"],
)
def test_owner_or_admin_credentials_in_the_api_file_are_reported(deploy, tmp_path: Path, line) -> None:
    (tmp_path / "env").write_text(f"DB_PASSWORD=x\n{line}\n")
    result, _calls = deploy()
    assert result.returncode == 0, result.stderr
    assert "the API container reads that file" in result.stderr, result.stderr
    assert "p-SENTINEL" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "line", ["# DB_OWNER_PASSWORD=p", "DB_OWNER_PASSWORD_FILE=/run/secret"], ids=["comment", "other-name"]
)
def test_lookalikes_are_not_reported(deploy, tmp_path: Path, line) -> None:
    (tmp_path / "env").write_text(f"DB_PASSWORD=x\n{line}\n")
    result, _calls = deploy()
    assert result.returncode == 0, result.stderr
    assert "the API container reads that file" not in result.stderr


def test_runtime_credentials_alone_are_not_reported(deploy) -> None:
    result, _calls = deploy()
    assert result.returncode == 0, result.stderr
    assert "the API container reads that file" not in result.stderr
