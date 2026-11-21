"""Who can reach a self-hosted TCKDB, and through which port.

Pinned here:

* only the web UI and Caddy publish a port off loopback. Every other
  service's published port names 127.0.0.1 literally, so no setting can move
  it, and no service but cloudflared shares the host's network;
* rendered by ``docker compose config`` with the web UI on every interface
  and every profile on, the database, the object store and the API port are
  still on 127.0.0.1;
* ``tckdb_make_env.sh --access`` sets the bind, the profiles, the URL, the
  mode, the cookies and the proxy header together, and compose run from the
  files it writes starts the services and ports that access implies;
* the Caddyfile replaces X-Real-IP and has a snippet for every tls choice the
  compose file can name, and caddy refuses to start without a domain;
* the frontend image believes X-Real-IP only from its own networks.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
COMPOSE = yaml.safe_load(COMPOSE_FILE.read_text())
SERVICES = COMPOSE["services"]
MAKE_ENV = REPO_ROOT / "backend/scripts/ops/tckdb_make_env.sh"
CADDYFILE = REPO_ROOT / "deploy/caddy/Caddyfile"
FRONTEND = REPO_ROOT / "frontend"
REAL_IP_SCRIPT = FRONTEND / "docker-entrypoint.d/15-tckdb-real-ip-from.sh"

WEB_PORT = "${TCKDB_WEB_BIND:-127.0.0.1}:${TCKDB_WEB_PORT:-8011}:8080"
CADDY_PORTS = [
    "${TCKDB_HTTP_PORT:-80}:80",
    "${TCKDB_HTTPS_PORT:-443}:443",
    "${TCKDB_HTTPS_PORT:-443}:443/udp",
]
ALL_PROFILES = "selfhost,caddy,cloudflare,worker,minio"
SECRETS = "DB_PASSWORD=x\nDB_ADMIN_PASSWORD=x\nS3_SECRET_KEY=x\n"


# ---------------------------------------------------------------------------
# Published ports, as written
# ---------------------------------------------------------------------------


def test_only_the_web_ui_and_caddy_publish_off_loopback() -> None:
    published = {name: svc["ports"] for name, svc in SERVICES.items() if svc.get("ports")}
    assert published.pop("frontend") == [WEB_PORT]
    assert published.pop("caddy") == CADDY_PORTS
    assert published, "no other service publishes a port; the check below would pass on nothing"
    for name, ports in published.items():
        for port in ports:
            assert port.startswith("127.0.0.1:"), f"{name} publishes {port!r} off loopback"


def test_only_cloudflared_shares_the_host_network() -> None:
    on_host = {name for name, svc in SERVICES.items() if svc.get("network_mode") == "host"}
    assert on_host == {"cloudflared"}


# ---------------------------------------------------------------------------
# Published ports, as Compose renders them
# ---------------------------------------------------------------------------


def _compose_config(project_dir: Path) -> dict:
    """``docker compose config`` for env files in *project_dir*, no --profile."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("docker is not installed, so the rendered compose ports cannot be checked")
    args = [docker, "compose", "-f", str(COMPOSE_FILE), "--project-directory", str(project_dir)]
    for name in (".env.selfhosted", ".env.db-admin"):
        args += ["--env-file", str(project_dir / name)]
    # Only the env files decide; nothing from the caller's shell leaks in.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(project_dir)}
    result = subprocess.run(
        [*args, "config", "--format", "json"], capture_output=True, text=True, timeout=60, env=env
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


def _host_ips(services: dict) -> dict[str, set[str]]:
    """Service -> host addresses its ports bind to ("*" for every interface)."""
    return {
        name: {port.get("host_ip", "*") for port in svc["ports"]}
        for name, svc in services.items()
        if svc.get("ports")
    }


def test_data_ports_stay_on_loopback_with_the_web_ui_on_every_interface(tmp_path: Path) -> None:
    (tmp_path / ".env.selfhosted").write_text(
        f"{SECRETS}COMPOSE_PROFILES={ALL_PROFILES}\nTCKDB_WEB_BIND=0.0.0.0\nTCKDB_DOMAIN=tckdb.example.org\n"
    )
    (tmp_path / ".env.db-admin").write_text("")
    services = _compose_config(tmp_path)
    assert {"db", "seaweedfs", "minio", "tckdb-api", "frontend", "caddy", "cloudflared", "worker"} <= services.keys()
    ips = _host_ips(services)
    assert ips.pop("frontend") == {"0.0.0.0"}
    assert ips.pop("caddy") == {"*"}
    assert {"db", "seaweedfs", "minio", "tckdb-api"} <= ips.keys()
    assert all(found == {"127.0.0.1"} for found in ips.values()), ips


def test_the_web_ui_defaults_to_loopback(tmp_path: Path) -> None:
    (tmp_path / ".env.selfhosted").write_text(f"{SECRETS}COMPOSE_PROFILES=selfhost\n")
    (tmp_path / ".env.db-admin").write_text("")
    assert _host_ips(_compose_config(tmp_path))["frontend"] == {"127.0.0.1"}


# ---------------------------------------------------------------------------
# tckdb_make_env.sh --access, end to end
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


SELFHOST = {"db", "seaweedfs", "init", "tckdb-api", "frontend"}

ACCESS_CASES = {
    "local": (
        [],
        {
            "DEPLOYMENT_MODE": "shared_private",
            "TCKDB_PUBLIC_BASE_URL": "http://127.0.0.1:8011/api/v1",
            "COMPOSE_PROFILES": "selfhost",
            "SESSION_COOKIE_SECURE": "true",
        },
        {"TRUSTED_PROXY_HEADER", "TCKDB_WEB_BIND", "TCKDB_DOMAIN", "SESSION_COOKIE_ALLOW_PLAIN_HTTP"},
        SELFHOST,
        "127.0.0.1",
        "--profile selfhost up -d --wait",
    ),
    "lan": (
        ["--lan-address", "192.168.50.10"],
        {
            "DEPLOYMENT_MODE": "shared_private",
            "TCKDB_PUBLIC_BASE_URL": "http://192.168.50.10:8011/api/v1",
            "TCKDB_WEB_BIND": "192.168.50.10",
            "COMPOSE_PROFILES": "selfhost",
            "TRUSTED_PROXY_HEADER": "X-Real-IP",
            "SESSION_COOKIE_SECURE": "false",
            "SESSION_COOKIE_ALLOW_PLAIN_HTTP": "true",
        },
        {"TCKDB_DOMAIN"},
        SELFHOST,
        "192.168.50.10",
        "--profile selfhost up -d --wait",
    ),
    "https": (
        ["--domain", "tckdb.example.org", "--acme-email", "admin@example.org"],
        {
            "DEPLOYMENT_MODE": "hosted_public",
            "TCKDB_PUBLIC_BASE_URL": "https://tckdb.example.org/api/v1",
            "TCKDB_DOMAIN": "tckdb.example.org",
            "TCKDB_ACME_EMAIL": "admin@example.org",
            "COMPOSE_PROFILES": "selfhost,caddy",
            "TRUSTED_PROXY_HEADER": "X-Real-IP",
            "SESSION_COOKIE_SECURE": "true",
        },
        {"TCKDB_WEB_BIND", "SESSION_COOKIE_ALLOW_PLAIN_HTTP"},
        SELFHOST | {"caddy"},
        "127.0.0.1",
        "--profile selfhost --profile caddy up -d --wait",
    ),
    "cloudflare": (
        ["--domain", "tckdb.example.org"],
        {
            "DEPLOYMENT_MODE": "hosted_public",
            "TCKDB_PUBLIC_BASE_URL": "https://tckdb.example.org/api/v1",
            "COMPOSE_PROFILES": "selfhost,cloudflare",
            "TRUSTED_PROXY_HEADER": "CF-Connecting-IP",
            "SESSION_COOKIE_SECURE": "true",
        },
        {"TCKDB_WEB_BIND", "SESSION_COOKIE_ALLOW_PLAIN_HTTP"},
        SELFHOST | {"cloudflared"},
        "127.0.0.1",
        "--profile selfhost --profile cloudflare up -d --wait",
    ),
}


@pytest.mark.parametrize("access", sorted(ACCESS_CASES))
def test_make_env_access_sets_everything_together(tmp_path: Path, access: str) -> None:
    args, expected, absent, services, web_bind, command = ACCESS_CASES[access]
    result = _make_env(tmp_path, "--access", access, *args)
    assert result.returncode == 0, result.stderr
    env = _values(tmp_path / ".env.selfhosted")
    assert {key: env.get(key) for key in expected} == expected
    assert not absent & env.keys(), absent & env.keys()
    assert f"docker compose --env-file .env.selfhosted --env-file .env.db-admin {command}" in result.stdout

    rendered = _compose_config(tmp_path)
    assert set(rendered) == services
    ips = _host_ips(rendered)
    assert ips.pop("frontend") == {web_bind}
    if "caddy" in services:
        assert ips.pop("caddy") == {"*"}
    assert all(found == {"127.0.0.1"} for found in ips.values()), ips


def test_make_env_access_flags_can_be_overridden(tmp_path: Path) -> None:
    result = _make_env(
        tmp_path, "--access", "https", "--domain", "tckdb.example.org",
        "--mode", "shared_private", "--proxy-header", "",
    )
    assert result.returncode == 0, result.stderr
    env = _values(tmp_path / ".env.selfhosted")
    assert env["DEPLOYMENT_MODE"] == "shared_private"
    assert "TRUSTED_PROXY_HEADER" not in env
    assert "TCKDB_ACME_EMAIL" not in env


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--access", "public"], "access must be local, lan, https or cloudflare"),
        (["--access", "lan", "--lan-address", "999.1.1.1"], "must be an IPv4 address"),
        (["--access", "lan", "--lan-address", "tckdb.lab"], "must be an IPv4 address"),
        (["--access", "lan", "--lan-address", "127.0.0.1"], "not 127.0.0.1"),
        (["--access", "lan", "--lan-address", "0.0.0.0"], "not 0.0.0.0"),
        (["--access", "https"], "needs a domain name"),
        (["--access", "cloudflare", "--domain", "https://tckdb.example.org"], "without http://"),
        (["--access", "https", "--domain", "tckdb.example.org", "--acme-email", "nobody"], "not an email"),
    ],
)
def test_make_env_rejects_bad_access_settings(tmp_path: Path, args: list[str], message: str) -> None:
    result = _make_env(tmp_path, *args)
    assert result.returncode == 2, result.stdout
    assert message in result.stderr
    assert not (tmp_path / ".env.selfhosted").exists()


@pytest.mark.parametrize(
    ("address", "public"),
    [("192.168.50.10", False), ("10.0.0.4", False), ("172.20.1.2", False), ("100.101.102.103", False),
     ("203.0.113.10", True), ("172.32.0.1", True)],
)
def test_make_env_warns_about_a_public_lan_address(tmp_path: Path, address: str, public: bool) -> None:
    result = _make_env(tmp_path, "--access", "lan", "--lan-address", address)
    assert result.returncode == 0, result.stderr
    assert (f"warning: {address} is a public address" in result.stdout) is public


# ---------------------------------------------------------------------------
# Caddy
# ---------------------------------------------------------------------------


def test_caddy_replaces_x_real_ip_and_proxies_to_the_web_ui() -> None:
    text = CADDYFILE.read_text()
    assert "reverse_proxy frontend:8080 {\n\t\theader_up X-Real-IP {remote_host}\n\t}" in text
    caddy = SERVICES["caddy"]
    assert "./deploy/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" in caddy["volumes"]
    assert caddy["depends_on"] == {"frontend": {"condition": "service_healthy"}}
    assert caddy["image"].startswith("caddy:") and "@sha256:" in caddy["image"]
    assert "networks" not in caddy, "caddy belongs on default only, never on storage"


@pytest.mark.parametrize(
    ("settings", "snippet"),
    [
        ("", "acme"),
        ("TCKDB_ACME_EMAIL=admin@example.org\n", "acme_email"),
        ("TCKDB_ACME_EMAIL=admin@example.org\nTCKDB_TLS=internal\n", "internal"),
    ],
)
def test_every_tls_choice_names_a_caddyfile_snippet(tmp_path: Path, settings: str, snippet: str) -> None:
    (tmp_path / ".env.selfhosted").write_text(
        f"{SECRETS}COMPOSE_PROFILES=selfhost,caddy\nTCKDB_DOMAIN=tckdb.example.org\n{settings}"
    )
    (tmp_path / ".env.db-admin").write_text("")
    rendered = _compose_config(tmp_path)["caddy"]["environment"]["TCKDB_CADDY_TLS"]
    assert (rendered or "acme") == snippet
    assert "import tls_{$TCKDB_CADDY_TLS:acme}" in CADDYFILE.read_text()
    assert f"\n(tls_{snippet}) {{\n" in CADDYFILE.read_text()


def _caddy_entrypoint() -> str:
    entrypoint = SERVICES["caddy"]["entrypoint"]
    assert entrypoint[:2] == ["/bin/sh", "-c"], entrypoint
    return entrypoint[2].replace("$$", "$")


@pytest.mark.parametrize("domain", ["", "tckdb.example.org"])
def test_caddy_refuses_to_start_without_a_domain(tmp_path: Path, domain: str) -> None:
    log = tmp_path / "caddy.log"
    fake = tmp_path / "caddy"
    fake.write_text(f'#!/bin/sh\necho "caddy $*" >> "{log}"\n')
    fake.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", _caddy_entrypoint()],
        env={"PATH": f"{tmp_path}:{os.environ['PATH']}", "TCKDB_DOMAIN": domain},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if domain:
        assert result.returncode == 0, result.stderr
        assert log.read_text() == "caddy run --config /etc/caddy/Caddyfile --adapter caddyfile\n"
    else:
        assert result.returncode == 78
        assert "caddy: TCKDB_DOMAIN is not set" in result.stderr
        assert not log.exists()


# ---------------------------------------------------------------------------
# The frontend image: X-Real-IP from its own networks only
# ---------------------------------------------------------------------------

ROUTES = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    "eth1\t000B000A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
    "eth1\t0000A8C0\t010B000A\t0003\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
)


def test_real_ip_list_holds_the_containers_own_subnets(tmp_path: Path) -> None:
    routes, out = tmp_path / "route", tmp_path / "real_ip_from.conf"
    routes.write_text(ROUTES)
    out.write_text("")
    result = subprocess.run(
        ["sh", str(REAL_IP_SCRIPT), str(routes), str(out)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    # The default route and the route through a gateway are not this container's networks.
    assert out.read_text() == "set_real_ip_from 172.18.0.0/16;\nset_real_ip_from 10.0.11.0/24;\n"


def test_real_ip_list_that_cannot_be_written_leaves_nginx_starting(tmp_path: Path) -> None:
    routes = tmp_path / "route"
    routes.write_text(ROUTES)
    result = subprocess.run(
        ["sh", str(REAL_IP_SCRIPT), str(routes), str(tmp_path / "missing" / "real_ip_from.conf")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "X-Real-IP is accepted from no address" in result.stderr


def test_nginx_replaces_x_real_ip_unless_the_peer_is_trusted() -> None:
    conf = (FRONTEND / "nginx.conf").read_text()
    assert "include /etc/nginx/tckdb/real_ip_from.conf;" in conf
    assert "real_ip_header X-Real-IP;" in conf
    assert "proxy_set_header X-Real-IP $remote_addr;" in conf
    assert "$http_x_real_ip" not in conf, "a client-sent X-Real-IP must never be copied through as is"
    assert "real_ip_recursive on" not in conf
    dockerfile = (FRONTEND / "Dockerfile").read_text()
    assert "COPY --chown=101:0 real_ip_from.conf /etc/nginx/tckdb/real_ip_from.conf" in dockerfile
    assert "COPY --chmod=755 docker-entrypoint.d/15-tckdb-real-ip-from.sh /docker-entrypoint.d/" in dockerfile
    placeholder = (FRONTEND / "real_ip_from.conf").read_text().splitlines()
    assert [line for line in placeholder if line.strip() and not line.startswith("#")] == []
    assert REAL_IP_SCRIPT.stat().st_mode & stat.S_IXUSR
