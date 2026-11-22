"""The ``selfhost`` compose profile and the env-file generator behind it.

Pinned here:

* the profile is opt-in: with no profile, compose starts only ``db`` and
  ``seaweedfs``, as before;
* the API gets the runtime env file only, never the operator credentials, and
  addresses Postgres and the store by service name;
* the ``init`` script, executed with stand-ins for ``python`` and ``alembic``,
  refuses placeholder secrets before touching the database, runs the role
  check / apply / migrate / check / bootstrap steps in order, and applies the
  role split only while the check fails;
* ``tckdb_make_env.sh`` leaves no placeholder, writes mode 600, refuses to
  overwrite, and turns off Secure cookies only for a plain-http lab URL.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]
MAKE_ENV = REPO_ROOT / "backend/scripts/ops/tckdb_make_env.sh"

SECRETS = {
    "DB_PASSWORD": "runtime-pw",
    "DB_ADMIN_PASSWORD": "admin-pw",
    "DB_OWNER_PASSWORD": "owner-pw",
    "S3_ACCESS_KEY": "access",
    "S3_SECRET_KEY": "secret",
    "SEAWEEDFS_JWT_KEY": "0123456789abcdef",
    "PAGING_CURSOR_KEY": "k" * 40,
}


def test_without_a_profile_only_the_data_services_start() -> None:
    unprofiled = {name for name, svc in SERVICES.items() if not svc.get("profiles")}
    assert unprofiled == {"db", "seaweedfs"}
    selfhost = {name for name, svc in SERVICES.items() if "selfhost" in svc.get("profiles", [])}
    assert selfhost == {"init", "tckdb-api", "frontend"}


def test_the_api_gets_runtime_settings_only_and_container_addresses() -> None:
    api = SERVICES["tckdb-api"]
    assert api["env_file"] == [".env.selfhosted"]
    assert api["environment"] == {
        "DB_HOST": "db",
        "DB_PORT": "5432",
        "S3_ENDPOINT_URL": "http://seaweedfs:9000",
    }
    assert api["depends_on"]["init"] == {"condition": "service_completed_successfully"}
    assert api["ports"] == ["127.0.0.1:${TCKDB_API_PORT:-8010}:8010"]
    assert SERVICES["frontend"]["ports"] == ["${TCKDB_WEB_BIND:-127.0.0.1}:${TCKDB_WEB_PORT:-8011}:8080"]
    assert SERVICES["init"]["env_file"] == [".env.selfhosted", ".env.db-admin"]


def test_the_worker_reaches_the_bundled_store_by_service_name() -> None:
    env = SERVICES["worker"]["environment"]
    assert env["S3_ENDPOINT_URL"] == "http://seaweedfs:9000"
    assert env["S3_ACCESS_KEY"] == "${S3_ACCESS_KEY:-tckdb}"
    assert env["S3_SECRET_KEY"] == "${S3_SECRET_KEY:-}"
    assert env["S3_BUCKET"] == "${S3_BUCKET:-tckdb-artifacts}"


def test_published_images_share_one_tag() -> None:
    for name in ("init", "tckdb-api", "worker"):
        assert SERVICES[name]["image"] == "laxzal/tckdb-api:${TCKDB_IMAGE_TAG:-0.4.0}", name
    assert SERVICES["frontend"]["image"] == "laxzal/tckdb-frontend:${TCKDB_IMAGE_TAG:-0.4.0}"


# ---------------------------------------------------------------------------
# The init script, executed
# ---------------------------------------------------------------------------


def _init_script() -> str:
    command = SERVICES["init"]["command"]
    assert command[:2] == ["sh", "-ec"], command
    # Compose turns ``$$`` into ``$`` before the container sees it.
    return command[2].replace("$$", "$")


@pytest.fixture
def run_init(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    # `python scripts/configure_database_roles.py check` fails while ROLES_OK is absent.
    (bindir / "python").write_text(
        "#!/bin/sh\n"
        f'echo "python $*" >> "{log}"\n'
        'if [ "$2" = check ] && [ ! -e "$ROLES_OK" ]; then echo "- owner role missing" >&2; exit 1; fi\n'
        'if [ "$2" = apply ]; then : > "$ROLES_OK"; fi\n'
        "exit 0\n"
    )
    (bindir / "alembic").write_text(f'#!/bin/sh\necho "alembic $*" >> "{log}"\n')
    for fake in bindir.iterdir():
        fake.chmod(0o755)

    def _run(**overrides: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        env = {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "ROLES_OK": str(tmp_path / "roles_ok"),
            "TCKDB_BOOTSTRAP_USERNAME": "admin",
            "TCKDB_BOOTSTRAP_EMAIL": "admin@example.org",
            **SECRETS,
            **overrides,
        }
        result = subprocess.run(
            ["sh", "-ec", _init_script()], env=env, capture_output=True, text=True, timeout=30
        )
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return result, calls

    return _run


@pytest.mark.parametrize("name", sorted(SECRETS))
@pytest.mark.parametrize("value", ["", "change-me-something"], ids=["empty", "change-me"])
def test_init_refuses_placeholder_secrets_before_touching_the_database(run_init, name, value) -> None:
    result, calls = run_init(**{name: value})
    assert result.returncode != 0
    assert f"init: {name} is empty or still a change-me placeholder" in result.stderr
    assert calls == []


def test_init_applies_the_role_split_only_while_the_check_fails(run_init) -> None:
    roles = "python scripts/configure_database_roles.py"
    bootstrap = "python scripts/bootstrap_admin.py --role admin --username admin --email admin@example.org"

    first, calls = run_init()
    assert first.returncode == 0, first.stderr
    assert calls == [f"{roles} check", f"{roles} apply", "alembic upgrade head", f"{roles} check", bootstrap]
    # The failed check's own report is kept, not swallowed.
    assert "- owner role missing" in first.stderr
    assert "init: role check failed; provisioning the role split" in first.stderr

    second, calls = run_init()
    assert second.returncode == 0, second.stderr
    assert calls == [f"{roles} check", "alembic upgrade head", f"{roles} check", bootstrap]


def test_init_omits_an_empty_email(run_init) -> None:
    result, calls = run_init(TCKDB_BOOTSTRAP_EMAIL="")
    assert result.returncode == 0, result.stderr
    assert calls[-1] == "python scripts/bootstrap_admin.py --role admin --username admin"


# ---------------------------------------------------------------------------
# tckdb_make_env.sh
# ---------------------------------------------------------------------------


def _make_env(out: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(MAKE_ENV), "--yes", "--dir", str(out), *args],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        env={k: v for k, v in os.environ.items() if k != "TCKDB_BOOTSTRAP_PASSWORD"},
    )


def _values(path: Path) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if line and not line.startswith("#"))
    return dict(pairs)


def test_make_env_fills_every_secret(tmp_path: Path) -> None:
    result = _make_env(tmp_path, "--admin-email", "admin@example.org", "--set", "S3_PORT=29000")
    assert result.returncode == 0, result.stderr
    runtime, admin = tmp_path / ".env.selfhosted", tmp_path / ".env.db-admin"
    for path in (runtime, admin):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "=change-me" not in path.read_text()
    env = _values(runtime) | _values(admin)
    for name in SECRETS:
        assert len(env[name]) >= 16, name
    assert len(env["PAGING_CURSOR_KEY"]) >= 32
    assert env["DEPLOYMENT_MODE"] == "shared_private"
    assert "TRUSTED_PROXY_HEADER" not in env
    assert env["S3_PORT"] == "29000"
    assert env["S3_ENDPOINT_URL"] == "http://127.0.0.1:29000"
    assert env["TCKDB_BOOTSTRAP_EMAIL"] == "admin@example.org"
    assert env["TCKDB_BOOTSTRAP_PASSWORD"] in result.stdout
    # Operator credentials stay out of the API's file.
    assert not {"DB_ADMIN_PASSWORD", "DB_OWNER_PASSWORD", "TCKDB_BOOTSTRAP_PASSWORD"} & _values(runtime).keys()
    # Loopback URL: Secure cookies work, nothing is relaxed.
    assert env["SESSION_COOKIE_SECURE"] == "true"
    assert "SESSION_COOKIE_ALLOW_PLAIN_HTTP" not in env


def test_make_env_refuses_to_overwrite(tmp_path: Path) -> None:
    assert _make_env(tmp_path).returncode == 0
    before = (tmp_path / ".env.selfhosted").read_text()
    again = _make_env(tmp_path)
    assert again.returncode == 1
    assert "already exists" in again.stderr
    assert (tmp_path / ".env.selfhosted").read_text() == before
    forced = _make_env(tmp_path, "--force")
    assert forced.returncode == 0
    assert "warning: overwriting" in forced.stderr
    assert (tmp_path / ".env.selfhosted").read_text() != before


def test_make_env_takes_the_admin_password_from_the_environment(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(MAKE_ENV), "--yes", "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "TCKDB_BOOTSTRAP_PASSWORD": "a-chosen-admin-password"},
    )
    assert result.returncode == 0, result.stderr
    assert _values(tmp_path / ".env.db-admin")["TCKDB_BOOTSTRAP_PASSWORD"] == "a-chosen-admin-password"
    # Chosen, not generated, so not echoed.
    assert "a-chosen-admin-password" not in result.stdout


@pytest.mark.parametrize(
    ("mode", "url", "secure", "allow"),
    [
        ("shared_private", "http://tckdb.lab.internal:8011", "false", "true"),
        ("shared_private", "https://tckdb.lab.internal", "true", None),
        ("hosted_public", "http://tckdb.example.org", "true", None),
    ],
)
def test_make_env_relaxes_secure_cookies_only_for_a_plain_http_lab(tmp_path, mode, url, secure, allow) -> None:
    result = _make_env(tmp_path, "--mode", mode, "--public-url", url, "--proxy-header", "X-Real-IP")
    assert result.returncode == 0, result.stderr
    env = _values(tmp_path / ".env.selfhosted")
    assert env["SESSION_COOKIE_SECURE"] == secure
    assert env.get("SESSION_COOKIE_ALLOW_PLAIN_HTTP") == allow
    assert env["TRUSTED_PROXY_HEADER"] == "X-Real-IP"
    assert env["TCKDB_PUBLIC_BASE_URL"] == f"{url}/api/v1"


def test_make_env_rejects_a_bad_mode(tmp_path: Path) -> None:
    result = _make_env(tmp_path, "--mode", "public")
    assert result.returncode == 2
    assert not (tmp_path / ".env.selfhosted").exists()
